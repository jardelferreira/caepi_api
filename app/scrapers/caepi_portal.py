"""Consulta o portal CAEPI usando Selenium/Chromium em modo headless."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.scrapers.errors import PortalAccessBlockedError

PORTAL_URL = os.getenv(
    "CAEPI_URL",
    "https://caepi.trabalho.gov.br/internet/ConsultaCAInternet.aspx",
)
LEGACY_PORTAL_URL = "http://caepi.mte.gov.br/internet/ConsultaCAInternet.aspx"
PDF_PATH = "/Relatorios/ImprimirCAPesquisaInternetextsharp.aspx"

FIELD_IDS: dict[str, str] = {
    "numero_ca": "lblNRRegistroCA",
    "data_validade": "lblDTValidade",
    "situacao": "lblSituacao",
    "processo": "lblNRProcesso",
    "cnpj": "lblNRCNPJ",
    "razao_social": "lblNORazaoSocial",
    "natureza": "lblNatureza",
    "equipamento": "lblNOEquipamento",
    "marcacao_ca": "lblDSLocalMarcacaoCA",
    "referencia": "lblDSReferencia",
    "tamanho": "lblDSTamanho",
    "descricao": "lblEquipamentoDSEquipamentoTexto",
    "cor": "lblDSCor",
    "numero_laudo": "grdListaLaudos_lblNrLaudo_0",
    "laudo": "lblDSAprovadoParaLaudo",
    "obs_laudo": "lblOBSAnaliseLaudo",
    "razao_social_laboratorio": "grdListaLaudos_lblNomeLaboratorioLaudo_0",
    "cnpj_laboratorio": "grdListaLaudos_lblNomeLaboratorio_0",
    "norma_tecnica": "grdListaNormas_lblNomeLaboratorioLaudo_0",
    "historico_alteracoes": "grdListaHistoricoAlteracao",
}

BLOCKING_SIGNS = (
    "captcha", "recaptcha", "hcaptcha", "cloudflare", "just a moment",
    "access denied", "acesso negado", "forbidden", "request blocked",
    "incapsula", "akamai", "unusual traffic",
)


def _cloudflare_challenge_body(title: str = "", source: str = "", body: str = "") -> bool:
    """Reconhece páginas de challenge sem tentar resolvê-las ou contorná-las."""
    combined = f"{title}\n{source}\n{body}".lower()
    return any(sign in combined for sign in (
        "just a moment", "cf-mitigated", "challenges.cloudflare.com",
        "challenge-platform", "cf-chl-",
    ))


def _probe_cloudflare_challenge(url: str, timeout: int = 8) -> bool:
    """Em caso de página Selenium vazia, verifica se o servidor devolve challenge CF.

    Esta função apenas identifica a resposta de proteção; não a contorna.
    """
    user_agent = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
    )
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            header = response.headers.get("Cf-Mitigated", "")
            body = response.read(8192).decode("utf-8", errors="replace")
            return header.lower() == "challenge" or _cloudflare_challenge_body(source=body)
    except urllib.error.HTTPError as exc:
        header = exc.headers.get("Cf-Mitigated", "") if exc.headers else ""
        try:
            body = exc.read(8192).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return header.lower() == "challenge" or _cloudflare_challenge_body(source=body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False



@dataclass(slots=True)
class ScrapedPdf:
    content: bytes
    source_url: str
    content_type: str = "application/pdf"


class CaepiPortalScraper:
    """Extrai dados e PDF do portal oficial via Selenium.

    Cada operação abre um navegador isolado. O Selenium é síncrono por natureza;
    as tarefas Celery chamam estes métodos diretamente no processo worker.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30_000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.timeout_s = max(1, round(timeout_ms / 1000))
        self.chromium_bin = os.getenv("CHROMIUM_BIN", "/usr/bin/chromium")
        self.chromedriver_bin = os.getenv("CHROMEDRIVER_BIN", "/usr/bin/chromedriver")

    @staticmethod
    def _normalize_ca(value: str) -> str:
        normalized = re.sub(r"\D", "", str(value or ""))
        if not normalized or len(normalized) > 32:
            raise ValueError("Informe um número de CA válido.")
        return normalized

    @staticmethod
    def _selector(suffix: str, tag: str = "") -> str:
        return f'{tag}[id$="{suffix}"]' if tag else f'[id$="{suffix}"]'

    @staticmethod
    def _save_form_diagnostic(driver: webdriver.Chrome) -> Path:
        debug_dir = Path(os.getenv("CAEPI_DEBUG_DIR", "/app/debug/caepi"))
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                driver.save_screenshot(str(debug_dir / f"formulario-{stamp}.png"))
            except WebDriverException:
                pass
            try:
                (debug_dir / f"formulario-{stamp}.html").write_text(
                    driver.page_source or "", encoding="utf-8"
                )
            except OSError:
                pass
            try:
                body = " ".join(driver.find_element(By.TAG_NAME, "body").text.split())[:2000]
            except WebDriverException:
                body = ""
            try:
                (debug_dir / f"formulario-{stamp}.txt").write_text(
                    f"URL: {getattr(driver, 'current_url', '')}\n"
                    f"Título: {getattr(driver, 'title', '')}\nTexto: {body}",
                    encoding="utf-8",
                )
            except OSError:
                pass
        except OSError:
            pass
        return debug_dir

    def _create_driver(self, download_dir: Path) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        options.binary_location = self.chromium_bin
        if self.headless:
            options.add_argument("--headless=new")
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--lang=pt-BR",
        ):
            options.add_argument(arg)
        # Alinha o User-Agent ao script Selenium de referência que já foi
        # validado pelo usuário no mesmo ambiente/container.
        user_agent = os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36",
        )
        options.add_argument(f"--user-agent={user_agent}")
        if os.getenv("IGNORE_HTTPS_ERRORS", "0") == "1":
            options.add_argument("--ignore-certificate-errors")
        options.add_experimental_option("prefs", {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": False,
        })
        service = Service(executable_path=self.chromedriver_bin)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(max(45, self.timeout_s))
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir.resolve())},
            )
        except WebDriverException:
            pass
        return driver

    def _open_details(self, driver: webdriver.Chrome, ca_number: str):
        errors: list[str] = []
        wait = WebDriverWait(driver, self.timeout_s)
        number_input = None
        last_url = PORTAL_URL
        for url in dict.fromkeys((PORTAL_URL, LEGACY_PORTAL_URL)):
            last_url = url
            try:
                driver.get(url)
                # Responde rápido a um challenge visível na página sem insistir em seletores.
                title = getattr(driver, "title", "") or ""
                source = getattr(driver, "page_source", "") or ""
                try:
                    body_text = " ".join(driver.find_element(By.TAG_NAME, "body").text.split())[:300]
                except WebDriverException:
                    body_text = ""
                if _cloudflare_challenge_body(title, source, body_text):
                    debug_dir = self._save_form_diagnostic(driver)
                    raise PortalAccessBlockedError(
                        "O portal CAEPI respondeu com um desafio do Cloudflare; "
                        "a consulta automática foi interrompida sem tentar contorná-lo. "
                        f"Diagnóstico salvo em {debug_dir}."
                    )
                number_input = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, self._selector("txtNumeroCA", "input"))
                ))
                break
            except PortalAccessBlockedError:
                raise
            except (TimeoutException, WebDriverException) as exc:
                excerpt = ""
                title = ""
                source = ""
                try:
                    title = getattr(driver, "title", "") or ""
                    source = getattr(driver, "page_source", "") or ""
                    excerpt = " ".join(driver.find_element(By.TAG_NAME, "body").text.split())[:180]
                except WebDriverException:
                    pass
                if _cloudflare_challenge_body(title, source, excerpt) or _probe_cloudflare_challenge(url):
                    debug_dir = self._save_form_diagnostic(driver)
                    raise PortalAccessBlockedError(
                        "O portal CAEPI respondeu com um desafio do Cloudflare (HTTP 403/Cf-Mitigated: challenge). "
                        "Não serão feitas novas tentativas automáticas para esse bloqueio. "
                        f"Diagnóstico salvo em {debug_dir}."
                    ) from exc
                errors.append(f"{url}: {type(exc).__name__}: {excerpt}")
        if number_input is None:
            debug_dir = self._save_form_diagnostic(driver)
            raise RuntimeError(
                "O formulário CAEPI não ficou disponível. "
                + " | ".join(errors)
                + f" | Diagnóstico salvo em {debug_dir}"
            )

        number_input.clear()
        number_input.send_keys(ca_number)
        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, self._selector("btnConsultar"))
        )).click()

        try:
            wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, self._selector("grdListaResultado_btnDetalhar_0"))
            )).click()
        except TimeoutException as exc:
            body = " ".join(driver.find_element(By.TAG_NAME, "body").text.split())[:500]
            lowered = body.lower()
            if any(term in lowered for term in (
                "nenhum", "não encontrad", "nao encontrad", "sem resultado",
            )):
                return None
            if any(sign in (driver.page_source + body).lower() for sign in BLOCKING_SIGNS):
                raise RuntimeError(
                    "O portal apresentou uma página de proteção/bloqueio; "
                    f"não foi possível consultar o CA. Conteúdo: {body}"
                ) from exc
            raise RuntimeError(
                f"Não foi possível abrir o resultado do CA {ca_number}. Conteúdo: {body}"
            ) from exc

        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, self._selector("lblNRRegistroCA"))
        ))
        return driver

    def _extract_fields(self, driver: webdriver.Chrome) -> dict[str, Any]:
        ref = driver.find_element(By.CSS_SELECTOR, self._selector("lblNRRegistroCA"))
        element_id = ref.get_attribute("id") or ""
        prefix = element_id.removesuffix("lblNRRegistroCA")
        fields: dict[str, Any] = {}
        extras: dict[str, str] = {}

        # Extrai rótulos no detalhe e preserva campos que ainda não têm coluna própria.
        labels = driver.execute_script(
            """
            const prefix = arguments[0];
            return [...document.querySelectorAll('span[id], label[id]')]
              .filter(e => e.id.startsWith(prefix) && !e.closest('table') && e.innerText.trim())
              .map(e => [e.id, e.innerText.trim().replace(/\\s+/g, ' ')]);
            """,
            prefix,
        )
        suffix_to_key = {suffix: key for key, suffix in FIELD_IDS.items()}
        for full_id, value in labels:
            suffix = full_id.removeprefix(prefix)
            key = suffix_to_key.get(suffix)
            if key:
                fields[key] = value
            elif value:
                extras[suffix] = value

        tables = driver.execute_script(
            """
            const prefix = arguments[0];
            return [...document.querySelectorAll('table[id]')]
              .filter(t => t.id.startsWith(prefix + 'grd'))
              .map(t => ({
                id: t.id,
                rows: [...t.querySelectorAll('tr')]
                  .map(tr => [...tr.querySelectorAll('th,td')]
                    .map(c => c.innerText.trim().replace(/\\s+/g, ' ')))
                  .filter(row => row.some(cell => cell))
              }));
            """,
            prefix,
        )
        table_data = {
            item["id"].removeprefix(prefix): item["rows"]
            for item in tables if item.get("rows")
        }
        if extras:
            fields["campos_extras"] = extras
        fields["tabelas"] = table_data

        # Algumas versões colocam conteúdo longo em divs, não em span/label.
        for key, suffix in FIELD_IDS.items():
            if key in fields:
                continue
            elements = driver.find_elements(By.CSS_SELECTOR, self._selector(suffix))
            if elements:
                value = " ".join(elements[0].text.split())
                if value:
                    fields[key] = value
        return fields

    def fetch_certificate(self, ca_number: str):
        from app.scrapers.base import ScrapedCertificate

        normalized = self._normalize_ca(ca_number)
        with tempfile.TemporaryDirectory(prefix="caepi-selenium-") as temp:
            driver = None
            try:
                driver = self._create_driver(Path(temp))
                page = self._open_details(driver, normalized)
                if page is None:
                    return None
                fields = self._extract_fields(driver)
                returned_number = re.sub(r"\D", "", str(fields.get("numero_ca", "")))
                if returned_number and returned_number != normalized:
                    raise RuntimeError(
                        f"O portal retornou o CA {returned_number}, mas foi solicitado {normalized}."
                    )
                if not fields.get("numero_ca"):
                    raise RuntimeError("A página de detalhe abriu, mas nenhum dado do CA foi extraído.")
                return ScrapedCertificate(
                    ca_number=normalized,
                    fields=fields,
                    source_url=driver.current_url or PORTAL_URL,
                    collected_at=datetime.now(timezone.utc),
                )
            finally:
                if driver is not None:
                    driver.quit()

    @staticmethod
    def _wait_for_pdf(download_dir: Path, timeout_s: int = 30) -> Path:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            partials = list(download_dir.glob("*.crdownload"))
            pdfs = [p for p in download_dir.glob("*.pdf") if p.is_file() and p.stat().st_size > 0]
            if pdfs and not partials:
                newest = max(pdfs, key=lambda p: p.stat().st_mtime)
                with newest.open("rb") as stream:
                    if stream.read(5) == b"%PDF-":
                        return newest
            time.sleep(0.25)
        raise TimeoutError("O PDF não foi baixado pelo Chromium dentro do tempo limite.")

    def fetch_pdf(self, ca_number: str) -> ScrapedPdf:
        normalized = self._normalize_ca(ca_number)
        with tempfile.TemporaryDirectory(prefix="caepi-pdf-") as temp:
            download_dir = Path(temp)
            driver = None
            try:
                driver = self._create_driver(download_dir)
                page = self._open_details(driver, normalized)
                if page is None:
                    raise LookupError(f"CA {normalized} não encontrado no portal.")
                button = WebDriverWait(driver, self.timeout_s).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, self._selector("btnVisualizarCA"))
                    )
                )
                button.click()
                pdf_path = self._wait_for_pdf(
                    download_dir,
                    timeout_s=max(30, self.timeout_s),
                )
                content = pdf_path.read_bytes()
                if not content.startswith(b"%PDF-"):
                    raise RuntimeError("O arquivo baixado não contém uma assinatura PDF válida.")
                return ScrapedPdf(
                    content=content,
                    source_url=driver.current_url or f"https://caepi.trabalho.gov.br{PDF_PATH}",
                )
            finally:
                if driver is not None:
                    driver.quit()

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ImportReport:
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_read: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    errors: list[str] = field(default_factory=list)


class OfficialBaseImporter:
    """Estrutura inicial; validar fixtures reais antes de processar a base oficial."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def validate_archive(self, archive_path: Path) -> None:
        if not archive_path.exists() or archive_path.stat().st_size == 0:
            raise ValueError("Arquivo de origem inexistente ou vazio")

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings


@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = "새 채팅"
    provider: str = "ollama"
    model: str = "qwen3:8b"
    messages: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add(self, role: str, content: str) -> None:
        self.messages.append(
            {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        )

    def to_markdown(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            f"**모델:** {self.provider} / {self.model}",
            f"**생성:** {self.created_at[:19].replace('T', ' ')}",
            "",
            "---",
            "",
        ]
        for msg in self.messages:
            emoji = "👤" if msg["role"] == "user" else "🤖"
            ts = msg.get("timestamp", "")[:19].replace("T", " ")
            lines += [
                f"### {emoji} {msg['role'].capitalize()}",
                f"*{ts}*",
                "",
                msg["content"],
                "",
                "---",
                "",
            ]
        return "\n".join(lines)

    def save_as_md(self, filename: Optional[str] = None) -> Path:
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"chat_{self.id}_{ts}.md"
        path = settings.RESULTS_DIR / filename
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatSession":
        return cls(
            id=data.get("id") or str(uuid.uuid4())[:8],
            title=data.get("title", "새 채팅"),
            provider=data.get("provider", "ollama"),
            model=data.get("model", ""),
            messages=data.get("messages", []),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class ChatStore:
    """채팅 세션을 디스크(JSON)에 영속화한다 — 새로고침 후에도 기록 유지."""

    def __init__(self, chats_dir: Optional[Path] = None):
        self.dir = chats_dir or settings.CHATS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def save(self, session: ChatSession) -> None:
        """세션을 저장한다. 메시지가 없으면 저장하지 않는다."""
        if not session.messages:
            return
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, session_id: str) -> Optional[ChatSession]:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return ChatSession.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except Exception:
            return None

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def list_sessions(self) -> list[dict]:
        """저장된 세션 목록을 최신순으로 반환한다 (id·title·created_at·count)."""
        items: list[dict] = []
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append(
                {
                    "id": data.get("id", path.stem),
                    "title": data.get("title", "새 채팅"),
                    "created_at": data.get("created_at", ""),
                    "count": len(data.get("messages", [])),
                }
            )
        return sorted(items, key=lambda x: x["created_at"], reverse=True)

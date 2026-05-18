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

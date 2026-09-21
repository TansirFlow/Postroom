"""
应用配置：统一从 backend/.env 读取（pydantic-settings）。
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 服务 ----------
    app_name: str = "Postroom"
    app_version: str = "1.0.0"
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8077
    reload: bool = False

    # ---------- 数据库 ----------
    db_path: str = str(DATA_DIR / "app.db")
    store_body_preview: bool = True
    body_preview_chars: int = 1500

    # ---------- SMTP ----------
    smtp_host: str = "smtp.example.com"
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_starttls: bool = False
    smtp_timeout: int = 30
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Postroom"
    smtp_max_retries: int = 2

    # ---------- 鉴权 / 配额 ----------
    admin_api_key: str = ""
    bootstrap_api_key: str = ""
    rate_limit_per_hour: int = 120
    max_attachment_bytes: int = 10 * 1024 * 1024

    # ---------- 多用户 / 登录会话 ----------
    # 首个管理员账号的用户名；密码留空则首次启动随机生成并写入 data/keys.txt
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""
    # 登录会话有效期（小时）
    session_ttl_hours: int = 72
    # 登录失败限流：同一用户名 + IP 每小时最多尝试次数
    login_rate_limit_per_hour: int = 20
    # 会话令牌同时写入 HttpOnly Cookie（便于同源前端，可关）
    session_cookie_name: str = "postroom_session"

    # ---------- 测试收件人（部署后由管理员在 .env 里填写，仓库不含真实值） ----------
    test_recipients: str = ""

    # ---------- 任务会话 / 免登录回复链接 ----------
    # 生成回复链接用的对外地址；留空则自动取请求的 host
    public_base_url: str = ""
    # 签名密钥；留空则自动生成并持久化到 data/reply_secret.txt
    reply_token_secret: str = ""
    # 回复链接默认有效期（天）
    reply_token_ttl_days: int = 30
    # 单个任务下用户回信频率上限（条/小时）
    reply_rate_limit_per_hour: int = 60
    # 单条消息最大长度
    message_max_chars: int = 4000

    # ---------- 站点信息 / 备案（部署后由管理员填写） ----------
    # 页脚悬挂的备案号，如 "苏ICP备2026000000号"；留空则页脚不显示
    icp_license: str = ""
    # 备案号点击跳转的地址，默认工信部备案管理系统
    icp_license_url: str = "https://beian.miit.gov.cn/"

    # ---------- CORS ----------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def test_recipient_list(self) -> list[str]:
        return [o.strip() for o in self.test_recipients.split(",") if o.strip()]

    @property
    def from_email(self) -> str:
        return self.smtp_from_email or self.smtp_user


settings = Settings()

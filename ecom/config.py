"""应用配置。原 `config/settings.py` 与 `config/__init__.py` 的导出合并到此。"""

from functools import lru_cache

from pydantic_settings import BaseSettings

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    app_name: str = "Multi-Agent E-Commerce System"
    debug: bool = False

    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.minimax.chat/v1"
    llm_model: str = "MiniMax-M1"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    feature_ttl_seconds: int = 86400

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "product_embeddings"

    # Database
    # 默认留空：应用启动不依赖数据库；仅在创建数据库会话时校验必须为 MySQL URL。
    # 测试库地址 ECOM_TEST_DATABASE_URL 只由 tests/conftest.py 读取，不进入应用配置。
    database_url: str = ""

    # 内部演示购物保护值，正式政策另行确认。
    commerce_session_ttl_seconds: int = 86400
    commerce_turn_lease_seconds: int = 120
    commerce_action_ttl_seconds: int = 600
    commerce_max_line_quantity: int = 10
    commerce_scan_limit: int = 200
    commerce_demo_enabled: bool = False
    commerce_demo_checkout_enabled: bool = False
    commerce_checkout_allowed_hosts: str = ""
    commerce_checkout_allowed_path: str = "/checkout/"

    # A/B Testing
    ab_test_enabled: bool = True
    ab_test_default_bucket_count: int = 100

    # Identity
    # 默认按最严格环境与关闭演示开关取值：未显式配置时不开放演示注册/登录。
    identity_environment: str = "production"
    identity_demo_mode: bool = False
    identity_issuer: str = ""
    identity_audience: str = ""
    identity_private_key_path: str = ""
    identity_public_keys_path: str = ""
    identity_active_kid: str = ""
    identity_csrf_secret: str = ""
    identity_access_token_ttl_seconds: int = 900
    identity_session_ttl_seconds: int = 2592000  # 30 天绝对到期
    identity_clock_skew_seconds: int = 30
    # 逗号分隔的可信前端来源；留空表示不允许任何跨站来源，且绝不使用 "*"。
    identity_allowed_origins: str = ""
    # 逗号分隔的可信反向代理 CIDR；只有直接来源命中时才解析 X-Forwarded-For。
    identity_trusted_proxy_networks: str = ""
    # 登录每邮箱 5 次失败 / 每 IP 30 次失败（15 分钟），注册每 IP 3 次（1 小时）。
    identity_login_email_limit: int = 5
    identity_login_ip_limit: int = 30
    identity_register_ip_limit: int = 3
    identity_refresh_session_limit: int = 30
    identity_login_window_seconds: int = 900
    identity_register_window_seconds: int = 3600
    identity_refresh_window_seconds: int = 60

    # Agent timeouts (seconds)
    agent_timeout_user_profile: float = 5.0
    agent_timeout_product_rec: float = 8.0
    agent_timeout_marketing_copy: float = 10.0
    agent_timeout_inventory: float = 5.0

    model_config = {"env_file": ".env", "env_prefix": "ECOM_"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()

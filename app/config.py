import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    b2b_base_url: str = os.getenv("B2B_BASE_URL", "http://b2b.internal")
    b2b_service_key: str = os.getenv("B2B_SERVICE_KEY", "dev-service-key")
    b2b_timeout_seconds: float = float(os.getenv("B2B_TIMEOUT_SECONDS", "5.0"))
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-jwt-secret-for-tests-32-bytes")
    jwt_public_key: str = os.getenv("JWT_PUBLIC_KEY", "")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://neomarket:neomarket@localhost:5432/neomarket",
    )


settings = Settings()

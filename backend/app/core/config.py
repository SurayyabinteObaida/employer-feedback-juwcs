from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    magic_link_expire_minutes: int = 30
    session_expire_days: int = 7
    frontend_url: str = "http://localhost:5173"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "Juw Employer Portal <juwcs.obe@gmail.com>"
    obe_db_url: str = ""
    admin_api_key: str = ""
    environment: str = "production"

    class Config:
        env_file = ".env"


settings = Settings()
from pydantic import BaseModel, Field, SecretStr


class PgVectorConfig(BaseModel):
    connection_string: SecretStr = Field(
        description="Connection string for PgVector database",
        default=None,
        json_schema_extra={"secret": True},
    )

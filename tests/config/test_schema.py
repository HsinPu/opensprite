import pytest
from pydantic import ValidationError

from opensprite.config.schema import StorageConfig


def test_storage_config_accepts_supported_types():
    memory = StorageConfig(type="memory", path="memory.db")
    sqlite = StorageConfig(type="sqlite", path="sessions.db")

    assert memory.type == "memory"
    assert sqlite.type == "sqlite"


def test_storage_config_rejects_unsupported_file_type():
    with pytest.raises(ValidationError):
        StorageConfig(type="file", path="sessions.db")

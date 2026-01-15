"""Tests for database migration check utility."""

import pytest
import tempfile
import os
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text

from utils.db_migrations import check_database_migration

# Patch targets for lazy imports in utils.db_migrations
PATCH_SCRIPT_DIR = "alembic.script.ScriptDirectory"
PATCH_CONFIG = "alembic.config.Config"


class TestCheckDatabaseMigration:
    """Tests for check_database_migration function."""

    def _create_test_engine(self, db_path: str):
        """Create a SQLite engine for testing."""
        return create_engine(f"sqlite:///{db_path}", echo=False)

    def _setup_alembic_version_table(self, engine, version: str = None):
        """Create alembic_version table and optionally insert a version."""
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """))
            if version:
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:ver)"),
                    {"ver": version}
                )
            conn.commit()

    def test_migration_ok_when_current_equals_head(self, tmp_path):
        """Migration check returns ok when current revision equals head."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Setup: current revision in DB
        current_rev = "d01c4c0f81ed"
        self._setup_alembic_version_table(engine, current_rev)
        
        # Mock ScriptDirectory to return same revision as head
        mock_script = MagicMock()
        mock_script.get_heads.return_value = [current_rev]
        mock_script.get_current_head.return_value = current_rev
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is True
            assert "ok" in message
            assert current_rev in message
        
        engine.dispose()

    def test_migration_warning_when_pending(self, tmp_path):
        """Migration check returns warning when there are pending migrations."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Setup: old revision in DB
        current_rev = "rev_old_abc123"
        head_rev = "rev_new_xyz789"
        self._setup_alembic_version_table(engine, current_rev)
        
        # Mock ScriptDirectory to return different head
        mock_script = MagicMock()
        mock_script.get_heads.return_value = [head_rev]
        mock_script.get_current_head.return_value = head_rev
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is False
            assert "pending" in message.lower()
            assert current_rev in message
            assert head_rev in message
        
        engine.dispose()

    def test_migration_warning_when_no_revision(self, tmp_path):
        """Migration check returns warning when database has no alembic revision."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Don't create alembic_version table - DB not initialized
        
        # Mock ScriptDirectory to return a head
        mock_script = MagicMock()
        mock_script.get_heads.return_value = ["head_rev_123"]
        mock_script.get_current_head.return_value = "head_rev_123"
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is False
            assert "no alembic revision" in message.lower() or "not initialized" in message.lower()
        
        engine.dispose()

    def test_migration_ok_when_no_migrations_defined(self, tmp_path):
        """Migration check returns ok when no migrations are defined."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Mock ScriptDirectory with no heads
        mock_script = MagicMock()
        mock_script.get_heads.return_value = []
        mock_script.get_current_head.return_value = None
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is True
            assert "no migrations defined" in message.lower()
        
        engine.dispose()

    def test_migration_ok_with_multiple_heads(self, tmp_path):
        """Migration check handles multiple heads correctly."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Setup: current revision matches one of the heads
        current_rev = "head_branch_a"
        self._setup_alembic_version_table(engine, current_rev)
        
        # Mock ScriptDirectory with multiple heads
        mock_script = MagicMock()
        mock_script.get_heads.return_value = ["head_branch_a", "head_branch_b"]
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is True
            assert current_rev in message
        
        engine.dispose()

    def test_migration_warning_with_multiple_heads_not_matching(self, tmp_path):
        """Migration check warns when current doesn't match any head."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Setup: current revision doesn't match any head
        current_rev = "old_rev"
        self._setup_alembic_version_table(engine, current_rev)
        
        # Mock ScriptDirectory with multiple heads
        mock_script = MagicMock()
        mock_script.get_heads.return_value = ["head_a", "head_b"]
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.return_value = mock_script
            
            ok, message = check_database_migration(engine)
            
            assert ok is False
            assert "pending" in message.lower()
            assert "heads" in message.lower()
        
        engine.dispose()

    def test_handles_script_loading_error(self, tmp_path):
        """Migration check handles errors when loading migration scripts."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.side_effect = Exception("Config not found")
            
            ok, message = check_database_migration(engine)
            
            assert ok is False
            assert "warning" in message.lower()
            assert "cannot load migration scripts" in message.lower()
        
        engine.dispose()

    def test_uses_provided_alembic_ini_path(self, tmp_path):
        """Migration check uses the provided alembic.ini path."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        custom_path = "/custom/path/alembic.ini"
        
        mock_script = MagicMock()
        mock_script.get_heads.return_value = []
        
        with patch(PATCH_CONFIG) as mock_config_class:
            with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
                mock_sd_class.from_config.return_value = mock_script
                
                check_database_migration(engine, alembic_ini_path=custom_path)
                
                mock_config_class.assert_called_once_with(custom_path)
        
        engine.dispose()

    def test_message_does_not_contain_sensitive_info(self, tmp_path):
        """Error messages don't contain sensitive information like passwords."""
        db_file = tmp_path / "test.db"
        engine = self._create_test_engine(str(db_file))
        
        # Simulate an error with sensitive info
        sensitive_error = "Connection failed: postgresql://user:secret_password@host/db"
        
        with patch(PATCH_SCRIPT_DIR) as mock_sd_class:
            mock_sd_class.from_config.side_effect = Exception(sensitive_error)
            
            ok, message = check_database_migration(engine)
            
            # Message is truncated to 100 chars for safety
            assert len(message) < 200
            assert ok is False
        
        engine.dispose()

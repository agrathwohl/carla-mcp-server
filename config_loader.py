#!/usr/bin/env python3
"""
Configuration loader for Carla MCP Server
Loads configuration from .env files and environment variables
"""
import os
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class Config:
    """Configuration management for Carla MCP Server"""

    def __init__(self, config_file: Optional[Path] = None):
        """Initialize configuration

        Args:
            config_file: Path to .env file (default: .env in project root)
        """
        self._config: Dict[str, Any] = {}

        # Find config file
        if config_file is None:
            config_file = Path(__file__).parent / ".env"

        self.config_file = config_file
        self._load_config()

    def _load_config(self):
        """Load configuration from file and environment"""
        # First, load from .env file if it exists
        if self.config_file.exists():
            try:
                self._load_env_file(self.config_file)
                logger.info(f"Loaded config from: {self.config_file}")
            except Exception as e:
                logger.warning(f"Failed to load config file {self.config_file}: {e}")

        # Environment variables override file config
        self._load_from_environment()

    def _load_env_file(self, file_path: Path):
        """Load configuration from .env file

        Args:
            file_path: Path to .env file
        """
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue

                # Parse key=value pairs
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]

                    self._config[key] = value

    def _load_from_environment(self):
        """Load configuration from environment variables"""
        # Load all environment variables that might be relevant
        for key in os.environ:
            if key.startswith('CARLA_') or key.startswith('MIXASSIST_'):
                self._config[key] = os.environ[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self._config.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean configuration value

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Boolean configuration value
        """
        value = self.get(key)
        if value is None:
            return default

        # Convert string to bool
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')

        return bool(value)

    def get_path(self, key: str, default: Optional[Path] = None) -> Optional[Path]:
        """Get path configuration value

        Args:
            key: Configuration key
            default: Default path if key not found

        Returns:
            Path object or None
        """
        value = self.get(key)
        if value is None:
            return default

        # Expand user home directory and environment variables
        path_str = os.path.expanduser(os.path.expandvars(value))
        return Path(path_str)

    def has(self, key: str) -> bool:
        """Check if configuration key exists

        Args:
            key: Configuration key

        Returns:
            True if key exists
        """
        return key in self._config

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values

        Returns:
            Dictionary of all configuration
        """
        return self._config.copy()


# Global configuration instance
_global_config: Optional[Config] = None


def get_config() -> Config:
    """Get global configuration instance

    Returns:
        Global Config instance
    """
    global _global_config
    if _global_config is None:
        _global_config = Config()
    return _global_config


def reload_config(config_file: Optional[Path] = None):
    """Reload configuration from file

    Args:
        config_file: Path to config file (optional)
    """
    global _global_config
    _global_config = Config(config_file)

"""
Redis caching service for application-level caching.

Provides caching for:
- DSPy compilation results
- Semantic evaluation results
- API responses
- User sessions
- Rate limiting
"""

import redis
import json
import pickle
from typing import Any, Optional, Union
from datetime import timedelta
import logging

from app.config import settings


logger = logging.getLogger(__name__)


class CacheService:
    """Redis-based caching service."""

    def __init__(self):
        """Initialize Redis connection."""
        self.redis_client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        """Connect to Redis server."""
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=False,  # Handle binary data
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            # Test connection
            self.redis_client.ping()
            logger.info(f"Connected to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None

    def is_connected(self) -> bool:
        """Check if Redis is connected."""
        if not self.redis_client:
            return False
        try:
            self.redis_client.ping()
            return True
        except redis.ConnectionError:
            return False

    # ============================================================================
    # Basic Key-Value Operations
    # ============================================================================

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if not self.is_connected():
            return None

        try:
            value = self.redis_client.get(key)
            if value is None:
                return None

            # Try to unpickle (for Python objects)
            try:
                return pickle.loads(value)
            except:
                # If unpickling fails, return as string
                return value.decode('utf-8') if isinstance(value, bytes) else value
        except Exception as e:
            logger.error(f"Cache get error for key {key}: {e}")
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache (will be pickled)
            ttl: Time to live in seconds (None = no expiration)

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            # Pickle the value for storage
            pickled_value = pickle.dumps(value)

            if ttl:
                self.redis_client.setex(key, ttl, pickled_value)
            else:
                self.redis_client.set(key, pickled_value)

            return True
        except Exception as e:
            logger.error(f"Cache set error for key {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Cache delete error for key {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            return bool(self.redis_client.exists(key))
        except Exception as e:
            logger.error(f"Cache exists error for key {key}: {e}")
            return False

    # ============================================================================
    # JSON Operations
    # ============================================================================

    def get_json(self, key: str) -> Optional[dict]:
        """
        Get JSON value from cache.

        Args:
            key: Cache key

        Returns:
            Parsed JSON dict or None
        """
        value = self.get(key)
        if value is None:
            return None

        try:
            if isinstance(value, str):
                return json.loads(value)
            return value
        except json.JSONDecodeError:
            return None

    def set_json(
        self,
        key: str,
        value: dict,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set JSON value in cache.

        Args:
            key: Cache key
            value: Dict to cache as JSON
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            json_str = json.dumps(value)
            return self.set(key, json_str, ttl)
        except Exception as e:
            logger.error(f"Cache set_json error for key {key}: {e}")
            return False

    # ============================================================================
    # Hash Operations (for structured data)
    # ============================================================================

    def hget(self, name: str, key: str) -> Optional[Any]:
        """
        Get value from hash.

        Args:
            name: Hash name
            key: Field key

        Returns:
            Field value or None
        """
        if not self.is_connected():
            return None

        try:
            value = self.redis_client.hget(name, key)
            if value is None:
                return None

            try:
                return pickle.loads(value)
            except:
                return value.decode('utf-8') if isinstance(value, bytes) else value
        except Exception as e:
            logger.error(f"Cache hget error for {name}:{key}: {e}")
            return None

    def hset(self, name: str, key: str, value: Any) -> bool:
        """
        Set value in hash.

        Args:
            name: Hash name
            key: Field key
            value: Field value

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            pickled_value = pickle.dumps(value)
            self.redis_client.hset(name, key, pickled_value)
            return True
        except Exception as e:
            logger.error(f"Cache hset error for {name}:{key}: {e}")
            return False

    def hgetall(self, name: str) -> dict:
        """
        Get all fields from hash.

        Args:
            name: Hash name

        Returns:
            Dict of all fields
        """
        if not self.is_connected():
            return {}

        try:
            data = self.redis_client.hgetall(name)
            result = {}
            for key, value in data.items():
                k = key.decode('utf-8') if isinstance(key, bytes) else key
                try:
                    result[k] = pickle.loads(value)
                except:
                    result[k] = value.decode('utf-8') if isinstance(value, bytes) else value
            return result
        except Exception as e:
            logger.error(f"Cache hgetall error for {name}: {e}")
            return {}

    # ============================================================================
    # List Operations (for queues)
    # ============================================================================

    def lpush(self, key: str, value: Any) -> bool:
        """
        Push value to left of list.

        Args:
            key: List key
            value: Value to push

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            pickled_value = pickle.dumps(value)
            self.redis_client.lpush(key, pickled_value)
            return True
        except Exception as e:
            logger.error(f"Cache lpush error for key {key}: {e}")
            return False

    def rpop(self, key: str) -> Optional[Any]:
        """
        Pop value from right of list.

        Args:
            key: List key

        Returns:
            Popped value or None
        """
        if not self.is_connected():
            return None

        try:
            value = self.redis_client.rpop(key)
            if value is None:
                return None

            try:
                return pickle.loads(value)
            except:
                return value.decode('utf-8') if isinstance(value, bytes) else value
        except Exception as e:
            logger.error(f"Cache rpop error for key {key}: {e}")
            return None

    def llen(self, key: str) -> int:
        """
        Get length of list.

        Args:
            key: List key

        Returns:
            List length
        """
        if not self.is_connected():
            return 0

        try:
            return self.redis_client.llen(key)
        except Exception as e:
            logger.error(f"Cache llen error for key {key}: {e}")
            return 0

    # ============================================================================
    # Set Operations (for unique collections)
    # ============================================================================

    def sadd(self, key: str, *values: Any) -> bool:
        """
        Add values to set.

        Args:
            key: Set key
            values: Values to add

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            pickled_values = [pickle.dumps(v) for v in values]
            self.redis_client.sadd(key, *pickled_values)
            return True
        except Exception as e:
            logger.error(f"Cache sadd error for key {key}: {e}")
            return False

    def sismember(self, key: str, value: Any) -> bool:
        """
        Check if value is in set.

        Args:
            key: Set key
            value: Value to check

        Returns:
            True if value is in set, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            pickled_value = pickle.dumps(value)
            return bool(self.redis_client.sismember(key, pickled_value))
        except Exception as e:
            logger.error(f"Cache sismember error for key {key}: {e}")
            return False

    # ============================================================================
    # Expiration Management
    # ============================================================================

    def expire(self, key: str, seconds: int) -> bool:
        """
        Set expiration on key.

        Args:
            key: Cache key
            seconds: Seconds until expiration

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            self.redis_client.expire(key, seconds)
            return True
        except Exception as e:
            logger.error(f"Cache expire error for key {key}: {e}")
            return False

    def ttl(self, key: str) -> int:
        """
        Get time to live for key.

        Args:
            key: Cache key

        Returns:
            Seconds until expiration (-1 = no expiration, -2 = key doesn't exist)
        """
        if not self.is_connected():
            return -2

        try:
            return self.redis_client.ttl(key)
        except Exception as e:
            logger.error(f"Cache ttl error for key {key}: {e}")
            return -2

    # ============================================================================
    # Pattern Operations
    # ============================================================================

    def keys(self, pattern: str = "*") -> list:
        """
        Get all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., "user:*")

        Returns:
            List of matching keys
        """
        if not self.is_connected():
            return []

        try:
            keys = self.redis_client.keys(pattern)
            return [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
        except Exception as e:
            logger.error(f"Cache keys error for pattern {pattern}: {e}")
            return []

    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        if not self.is_connected():
            return 0

        try:
            keys = self.keys(pattern)
            if keys:
                return self.redis_client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Cache delete_pattern error for pattern {pattern}: {e}")
            return 0

    # ============================================================================
    # Application-Specific Helpers
    # ============================================================================

    def cache_dspy_result(
        self,
        signature_name: str,
        input_hash: str,
        result: Any,
        ttl: int = 3600
    ) -> bool:
        """
        Cache DSPy signature result.

        Args:
            signature_name: Name of DSPy signature
            input_hash: Hash of input parameters
            result: DSPy result
            ttl: Time to live in seconds (default: 1 hour)

        Returns:
            True if successful, False otherwise
        """
        key = f"dspy:{signature_name}:{input_hash}"
        return self.set(key, result, ttl)

    def get_dspy_result(
        self,
        signature_name: str,
        input_hash: str
    ) -> Optional[Any]:
        """
        Get cached DSPy result.

        Args:
            signature_name: Name of DSPy signature
            input_hash: Hash of input parameters

        Returns:
            Cached result or None
        """
        key = f"dspy:{signature_name}:{input_hash}"
        return self.get(key)

    def cache_semantic_match(
        self,
        text1_hash: str,
        text2_hash: str,
        similarity: float,
        ttl: int = 86400
    ) -> bool:
        """
        Cache semantic similarity result.

        Args:
            text1_hash: Hash of first text
            text2_hash: Hash of second text
            similarity: Similarity score
            ttl: Time to live in seconds (default: 24 hours)

        Returns:
            True if successful, False otherwise
        """
        key = f"semantic:{text1_hash}:{text2_hash}"
        return self.set(key, similarity, ttl)

    def get_semantic_match(
        self,
        text1_hash: str,
        text2_hash: str
    ) -> Optional[float]:
        """
        Get cached semantic similarity.

        Args:
            text1_hash: Hash of first text
            text2_hash: Hash of second text

        Returns:
            Similarity score or None
        """
        key = f"semantic:{text1_hash}:{text2_hash}"
        return self.get(key)

    def flush_all(self) -> bool:
        """
        Flush all cache data (use with caution!).

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            self.redis_client.flushdb()
            logger.warning("Cache flushed: all data deleted")
            return True
        except Exception as e:
            logger.error(f"Cache flush error: {e}")
            return False


# Singleton instance
cache_service = CacheService()

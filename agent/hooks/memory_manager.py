"""XXXX Memory Manager - AWS Bedrock AgentCore Memory integration

Episodic memory is GLOBALLY SHARED across all users/sessions via boto3
precise write, while semantic and preference memories remain per-user.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Shared Episodic Memory constants ──
# All users' episodic memories are stored under this shared actor/session
# so the agent can learn from ALL conversations, not just one user's history.
SHARED_EPISODIC_ACTOR = "shared_global"
SHARED_EPISODIC_SESSION = "episodic_shared"


class MemoryManager:
    """Singleton-style memory manager for XXXX agent using AWS Bedrock AgentCore Memory.

    This manager provides:
    - Long-term memory storage across conversations
    - Semantic search for relevant past interactions
    - Automatic fact extraction and storage
    """

    def __init__(
        self,
        actor_id: str,
        memory_id: Optional[str] = None,
        region: Optional[str] = None,
    ):
        """
        Initialize the memory manager.

        Args:
            actor_id: The user/actor ID (typically parent_id)
            memory_id: AgentCore Memory ID (defaults to MEMORY_ID env var)
            region: AWS region (defaults to AWS_REGION env var)
        """
        self.actor_id = actor_id
        self.memory_id = memory_id or os.environ.get("MEMORY_ID")
        self.region = region or os.environ.get("AWS_REGION", "us-west-2")

        self._session_manager = None
        self._sessions: dict[str, object] = {}
        self._shared_episodic_session = None  # For shared episodic search
        self._boto3_client = None              # For precise episodic write
        self._episodic_strategy_id: str | None = None
        self._is_enabled = False

        # Buffer for last conversation turn (set by chat_stream, read by stop_hook)
        # This avoids the transcript timing issue where stop_hook fires before
        # the final assistant response is written to the transcript file.
        self._last_user_prompt: str = ""
        self._last_assistant_response: str = ""

        if not self.memory_id:
            logger.warning(
                "MEMORY_ID not set - memory features disabled. "
                "Run 'python scripts/setup_memory.py' to create a memory."
            )
            return

        self._initialize_session_manager()

    def _initialize_session_manager(self) -> None:
        """Initialize the MemorySessionManager and resolve episodic strategy ID."""
        try:
            from bedrock_agentcore.memory import MemorySessionManager

            self._session_manager = MemorySessionManager(
                memory_id=self.memory_id,
                region_name=self.region,
            )
            self._is_enabled = True
            logger.info(f"Memory manager initialized with memory_id={self.memory_id}")

            # Resolve episodic strategy ID for boto3 precise write
            self._resolve_episodic_strategy_id()

        except ImportError as e:
            logger.error(f"Failed to import bedrock_agentcore: {e}")
            self._is_enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize memory session manager: {e}")
            self._is_enabled = False

    def _resolve_episodic_strategy_id(self) -> None:
        """Fetch the episodic strategy ID from AgentCore Memory config."""
        try:
            import boto3
            client = boto3.client('bedrock-agentcore-control', region_name=self.region)
            memory = client.get_memory(memoryId=self.memory_id)
            for strategy in memory.get('memory', {}).get('strategies', []):
                if strategy.get('type') == 'EPISODIC':
                    self._episodic_strategy_id = strategy.get('strategyId')
                    logger.info(f"Resolved episodic strategy ID: {self._episodic_strategy_id}")
                    break
            if not self._episodic_strategy_id:
                logger.warning("No EPISODIC strategy found in memory config")
        except Exception as e:
            logger.warning(f"Could not resolve episodic strategy ID: {e}")

    def _get_boto3_client(self):
        """Lazy-init boto3 data-plane client for precise episodic writes."""
        if self._boto3_client is None:
            import boto3
            self._boto3_client = boto3.client(
                'bedrock-agentcore', region_name=self.region
            )
        return self._boto3_client

    def _get_or_create_shared_episodic_session(self):
        """Get or create a shared session for global episodic memory search."""
        if self._shared_episodic_session is not None:
            return self._shared_episodic_session

        if not self._is_enabled or not self._session_manager:
            return None

        try:
            self._shared_episodic_session = self._session_manager.create_memory_session(
                actor_id=SHARED_EPISODIC_ACTOR,
                session_id=SHARED_EPISODIC_SESSION,
            )
            logger.debug("Created shared episodic session")
        except Exception as e:
            logger.error(f"Failed to create shared episodic session: {e}")
            return None

        return self._shared_episodic_session

    @property
    def is_enabled(self) -> bool:
        """Check if memory features are enabled."""
        return self._is_enabled

    def set_last_turn(self, user_prompt: str, assistant_response: str) -> None:
        """Buffer the last conversation turn for the stop hook to save."""
        self._last_user_prompt = user_prompt
        self._last_assistant_response = assistant_response

    def get_and_clear_last_turn(self) -> tuple[str, str]:
        """Get and clear the buffered conversation turn."""
        prompt = self._last_user_prompt
        response = self._last_assistant_response
        self._last_user_prompt = ""
        self._last_assistant_response = ""
        return prompt, response

    def _get_or_create_session(self, session_id: str):
        """Get or create a memory session for the given session_id."""
        if not self._is_enabled or not self._session_manager:
            return None

        if session_id not in self._sessions:
            try:
                session = self._session_manager.create_memory_session(
                    actor_id=self.actor_id,
                    session_id=session_id,
                )
                self._sessions[session_id] = session
                logger.debug(f"Created memory session: {session_id}")
            except Exception as e:
                logger.error(f"Failed to create memory session: {e}")
                return None

        return self._sessions.get(session_id)

    def search_memories(
        self,
        query: str,
        session_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """
        Search long-term memories for relevant information.

        Args:
            query: The search query
            session_id: The current session ID
            top_k: Maximum number of results to return

        Returns:
            List of memory records with text and metadata
        """
        if not self._is_enabled:
            return []

        session = self._get_or_create_session(session_id)
        if not session:
            return []

        try:
            records = session.search_long_term_memories(
                query=query,
                namespace_prefix="/",
                top_k=top_k,
            )

            results = []
            for record in records:
                # Record structure: record._data = {content: {text: ...}, score: ..., namespaces: [...]}
                data = getattr(record, "_data", {}) or {}
                content = data.get("content", {}) or {}
                text = content.get("text", "")
                score = data.get("score")
                namespaces = data.get("namespaces", [])

                results.append({
                    "text": text,
                    "score": score,
                    "namespace": namespaces[0] if namespaces else None,
                })

            logger.debug(f"Found {len(results)} memories for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            return []

    def save_conversation_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
    ) -> bool:
        """
        Save a conversation turn to memory.

        - Semantic + Preference: saved via per-user session (add_turns)
        - Episodic: saved via boto3 precise write to shared global namespace

        Args:
            session_id: The current session ID
            user_message: The user's message
            assistant_response: The assistant's response

        Returns:
            True if saved successfully, False otherwise
        """
        if not self._is_enabled:
            return False

        session = self._get_or_create_session(session_id)
        if not session:
            return False

        try:
            from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

            # 1. Per-user session: saves to semantic + preference strategies
            session.add_turns(
                messages=[
                    ConversationalMessage(
                        text=user_message,
                        role=MessageRole.USER,
                    ),
                    ConversationalMessage(
                        text=assistant_response,
                        role=MessageRole.ASSISTANT,
                    ),
                ]
            )
            logger.debug(f"Saved semantic/preference turn to session: {session_id}")

            # 2. Shared episodic: boto3 precise write (no semantic/preference side-effect)
            self._save_shared_episodic(user_message, assistant_response)

            return True

        except Exception as e:
            logger.error(f"Failed to save conversation turn: {e}")
            return False

    def _save_shared_episodic(self, user_message: str, assistant_response: str) -> None:
        """
        Write episodic memory to the shared global namespace via boto3.

        This bypasses session.add_turns() to avoid creating unwanted
        semantic/preference entries under the shared actor.

        Namespace: /strategies/{strategyId}/actors/shared_global/sessions/episodic_shared/
        """
        if not self._episodic_strategy_id:
            logger.debug("Episodic strategy ID not resolved, skipping shared episodic save")
            return

        try:
            client = self._get_boto3_client()
            namespace = (
                f"/strategies/{self._episodic_strategy_id}"
                f"/actors/{SHARED_EPISODIC_ACTOR}"
                f"/sessions/{SHARED_EPISODIC_SESSION}/"
            )

            client.create_memory_event(
                memoryId=self.memory_id,
                actorId=SHARED_EPISODIC_ACTOR,
                eventData={
                    'conversationalEvent': {
                        'conversationTurns': [
                            {'role': 'user', 'content': user_message},
                            {'role': 'assistant', 'content': assistant_response},
                        ]
                    }
                },
            )
            logger.debug(f"Saved shared episodic memory to namespace: {namespace}")

        except Exception as e:
            # Non-fatal: episodic write failure should not break the save flow
            logger.warning(f"Failed to save shared episodic memory: {e}")

    def get_user_preferences(self, session_id: str) -> list[dict]:
        """
        Get user preferences from the UserPreferenceStrategy namespace.

        Args:
            session_id: The current session ID

        Returns:
            List of user preference records
        """
        if not self._is_enabled:
            return []

        session = self._get_or_create_session(session_id)
        if not session:
            return []

        try:
            # Search in the user preferences namespace
            # UserPreferenceStrategy stores data in /users/{actorId}/preferences/
            records = session.list_long_term_memory_records(
                namespace_prefix=f"/users/{self.actor_id}/preferences/",
            )

            results = []
            for record in records:
                data = getattr(record, "_data", {}) or {}
                content = data.get("content", {}) or {}
                text = content.get("text", "")
                namespaces = data.get("namespaces", [])

                if text:
                    results.append({
                        "text": text,
                        "namespace": namespaces[0] if namespaces else None,
                    })

            logger.debug(f"Found {len(results)} user preferences for actor: {self.actor_id}")
            return results

        except Exception as e:
            # list_long_term_memory_records may not be available in all versions
            logger.debug(f"Failed to get user preferences (may not be supported): {e}")
            return []

    def search_user_preferences(
        self,
        query: str,
        session_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """
        Search user preferences for relevant information.

        Args:
            query: The search query
            session_id: The current session ID
            top_k: Maximum number of results to return

        Returns:
            List of preference records with text and metadata
        """
        if not self._is_enabled:
            return []

        session = self._get_or_create_session(session_id)
        if not session:
            return []

        try:
            # Search specifically in the user preferences namespace
            records = session.search_long_term_memories(
                query=query,
                namespace_prefix=f"/users/{self.actor_id}/preferences/",
                top_k=top_k,
            )

            results = []
            for record in records:
                data = getattr(record, "_data", {}) or {}
                content = data.get("content", {}) or {}
                text = content.get("text", "")
                score = data.get("score")
                namespaces = data.get("namespaces", [])

                if text:
                    results.append({
                        "text": text,
                        "score": score,
                        "namespace": namespaces[0] if namespaces else None,
                        "type": "preference",
                    })

            logger.debug(f"Found {len(results)} preference matches for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.debug(f"Failed to search user preferences: {e}")
            return []

    def search_episodic_memories(
        self,
        query: str,
        session_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """
        Search GLOBALLY SHARED episodic memories for interaction history.

        Uses the shared episodic session (actor_id=shared_global) so results
        come from ALL users' conversations, not just the current user.

        Args:
            query: The search query
            session_id: Unused (kept for API compatibility), search is global
            top_k: Maximum number of results to return

        Returns:
            List of episodic memory records
        """
        if not self._is_enabled:
            return []

        session = self._get_or_create_shared_episodic_session()
        if not session:
            # Fallback to per-user session if shared session fails
            session = self._get_or_create_session(session_id)
            if not session:
                return []

        try:
            records = session.search_long_term_memories(
                query=query,
                namespace_prefix="/strategies/",
                top_k=top_k,
            )

            results = []
            for record in records:
                data = getattr(record, "_data", {}) or {}
                content = data.get("content", {}) or {}
                text = content.get("text", "")
                score = data.get("score")
                namespaces = data.get("namespaces", [])

                if text:
                    results.append({
                        "text": text,
                        "score": score,
                        "namespace": namespaces[0] if namespaces else None,
                        "type": "episodic",
                    })

            logger.debug(f"Found {len(results)} episodic memories for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.debug(f"Failed to search episodic memories: {e}")
            return []

    def search_all_memories(
        self,
        query: str,
        session_id: str,
        top_k: int = 3,
    ) -> dict:
        """
        Search all memory categories: semantic, preferences, and episodic.

        This is the recommended method for the search_long_term_memory tool,
        as it provides comprehensive context from all memory types.

        Args:
            query: The search query
            session_id: The current session ID
            top_k: Maximum number of results per category

        Returns:
            Dict with keys: 'semantic', 'preferences', 'episodic'
        """
        return {
            "semantic": self.search_memories(query, session_id, top_k),
            "preferences": self.search_user_preferences(query, session_id, top_k),
            "episodic": self.search_episodic_memories(query, session_id, top_k),
        }

    def format_memories_as_context(
        self,
        memories: list[dict],
        preferences: list[dict] | None = None,
        episodic: list[dict] | None = None,
    ) -> str:
        """
        Format memory search results as context string for injection into prompts.

        Args:
            memories: List of semantic memory records
            preferences: List of user preference records (optional)
            episodic: List of episodic memory records (optional)

        Returns:
            Formatted context string
        """
        sections = []

        # Format user preferences if available
        if preferences:
            pref_lines = ["# 用户偏好 (User Preferences)"]
            for i, pref in enumerate(preferences, 1):
                text = pref.get("text", "")
                if text:
                    pref_lines.append(f"- {text}")
            if len(pref_lines) > 1:
                sections.append("\n".join(pref_lines))

        # Format episodic memories (interaction history)
        if episodic:
            ep_lines = ["# 历史交互记录 (Interaction History)"]
            for i, ep in enumerate(episodic, 1):
                text = ep.get("text", "")
                if text:
                    ep_lines.append(f"- {text}")
            if len(ep_lines) > 1:
                sections.append("\n".join(ep_lines))

        # Format semantic memories (general facts)
        if memories:
            mem_lines = ["# 相关事实 (Relevant Facts)"]
            for i, memory in enumerate(memories, 1):
                text = memory.get("text", "")
                if text:
                    mem_lines.append(f"{i}. {text}")
            if len(mem_lines) > 1:
                sections.append("\n".join(mem_lines))

        return "\n\n".join(sections)

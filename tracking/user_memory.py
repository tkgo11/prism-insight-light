"""
User Memory Manager for Telegram Bot

Persistent memory system for storing user-specific trading journals and conversation history.

Features:
- Record trading journals via /journal command
- Separate short-term memory (1 week) / long-term memory (beyond)
- Utilize memory in /evaluate and /report commands
- Support conversation threading via replies
- User-isolated storage (user_id based)
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class UserMemoryManager:
    """User-specific memory manager"""

    # Memory types
    MEMORY_JOURNAL = 'journal'
    MEMORY_EVALUATION = 'evaluation'
    MEMORY_REPORT = 'report'
    MEMORY_CONVERSATION = 'conversation'

    # Compression layers (same as existing pattern)
    LAYER_DETAILED = 1   # 0-7 days: Full content
    LAYER_SUMMARY = 2    # 8-30 days: Summary
    LAYER_COMPRESSED = 3  # 31+ days: Compressed

    # Token budget
    MAX_CONTEXT_TOKENS = 2000

    def __init__(self, db_path: str):
        """
        Initialize UserMemoryManager

        Args:
            db_path: SQLite database path
        """
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure tables exist and create if needed"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Create user_memories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    ticker TEXT,
                    ticker_name TEXT,
                    market_type TEXT DEFAULT 'kr',
                    importance_score REAL DEFAULT 0.5,
                    compression_layer INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    command_source TEXT,
                    message_id INTEGER,
                    tags TEXT
                )
            """)

            # Create user_preferences table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    preferred_tone TEXT DEFAULT 'neutral',
                    investment_style TEXT,
                    favorite_tickers TEXT,
                    total_evaluations INTEGER DEFAULT 0,
                    total_journals INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_active_at TEXT
                )
            """)

            # Create indexes
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_memories_type ON user_memories(user_id, memory_type)",
                "CREATE INDEX IF NOT EXISTS idx_memories_ticker ON user_memories(user_id, ticker)",
                "CREATE INDEX IF NOT EXISTS idx_memories_created ON user_memories(user_id, created_at DESC)",
            ]
            for idx_sql in indexes:
                cursor.execute(idx_sql)

            conn.commit()
            conn.close()
            logger.info("User memory tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize user memory tables: {e}")

    def _get_connection(self):
        """Return database connection"""
        return sqlite3.connect(self.db_path)

    # =========================================================================
    # Core Methods
    # =========================================================================

    def save_memory(
        self,
        user_id: int,
        memory_type: str,
        content: Dict[str, Any],
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = 'kr',
        importance_score: float = 0.5,
        command_source: Optional[str] = None,
        message_id: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> int:
        """
        Save memory

        Args:
            user_id: User ID
            memory_type: Memory type (journal, evaluation, report, conversation)
            content: Content to save (dict -> JSON)
            ticker: Stock code/ticker
            ticker_name: Stock name
            market_type: Market type (kr, us)
            importance_score: Importance score (0.0 ~ 1.0)
            command_source: Command source
            message_id: Telegram message ID
            tags: Tag list

        Returns:
            int: Created memory ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()
            content_json = json.dumps(content, ensure_ascii=False)
            tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

            cursor.execute("""
                INSERT INTO user_memories (
                    user_id, memory_type, content, ticker, ticker_name,
                    market_type, importance_score, compression_layer,
                    created_at, last_accessed_at, command_source, message_id, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, memory_type, content_json, ticker, ticker_name,
                market_type, importance_score, self.LAYER_DETAILED,
                now, now, command_source, message_id, tags_json
            ))

            memory_id = cursor.lastrowid or 0
            conn.commit()

            # Update user statistics
            self._update_user_stats(user_id, memory_type)

            logger.info(f"Memory saved: user={user_id}, type={memory_type}, ticker={ticker}, id={memory_id}")
            return memory_id

        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_memories(
        self,
        user_id: int,
        memory_type: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 10,
        include_compressed: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories

        Args:
            user_id: User ID
            memory_type: Memory type (None for all)
            ticker: Stock code/ticker (None for all)
            limit: Maximum number of results
            include_compressed: Whether to include compressed memories

        Returns:
            List[Dict]: Memory list
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            query = """
                SELECT id, user_id, memory_type, content, summary, ticker, ticker_name,
                       market_type, importance_score, compression_layer, created_at,
                       last_accessed_at, command_source, message_id, tags
                FROM user_memories
                WHERE user_id = ?
            """
            params: List[Any] = [user_id]

            if memory_type:
                query += " AND memory_type = ?"
                params.append(memory_type)

            if ticker:
                query += " AND ticker = ?"
                params.append(ticker)

            if not include_compressed:
                query += " AND compression_layer < ?"
                params.append(self.LAYER_COMPRESSED)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            memories = []
            for row in rows:
                memory = {
                    'id': row[0],
                    'user_id': row[1],
                    'memory_type': row[2],
                    'content': json.loads(row[3]) if row[3] else {},
                    'summary': row[4],
                    'ticker': row[5],
                    'ticker_name': row[6],
                    'market_type': row[7],
                    'importance_score': row[8],
                    'compression_layer': row[9],
                    'created_at': row[10],
                    'last_accessed_at': row[11],
                    'command_source': row[12],
                    'message_id': row[13],
                    'tags': json.loads(row[14]) if row[14] else []
                }
                memories.append(memory)

            # Update access time
            if memories:
                memory_ids = [m['id'] for m in memories]
                self._update_access_time(memory_ids)

            return memories

        except Exception as e:
            logger.error(f"Failed to get memories: {e}")
            return []
        finally:
            conn.close()

    def build_llm_context(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        max_tokens: int = 4000,
        user_message: Optional[str] = None
    ) -> str:
        """
        Build memory context for LLM

        Args:
            user_id: User ID
            ticker: Stock code/ticker (prioritize memories for specific stock)
            max_tokens: Maximum token count (default 4000)
            user_message: Current user message (for ticker extraction)

        Returns:
            str: Formatted memory context
        """
        parts = []
        tokens = 0
        loaded_tickers = set()  # Track already loaded tickers

        # Token estimation function (rough estimate for Korean text)
        def estimate_tokens(text: str) -> int:
            return len(text) // 2  # Korean: roughly 1 token per 2 characters

        # Priority 1: Journals for the specific ticker (max 1200 tokens)
        if ticker:
            journals = self.get_journals(user_id, ticker=ticker, limit=10)
            if journals:
                journal_text = self._format_journals(journals)
                journal_tokens = estimate_tokens(journal_text)
                if journal_tokens < 1200:
                    parts.append(f"📝 {ticker} Related Records:\n{journal_text}")
                    tokens += journal_tokens
                    loaded_tickers.add(ticker)

        # Priority 2: Past evaluations for the specific ticker (max 800 tokens)
        if ticker and tokens < max_tokens - 800:
            evals = self.get_memories(user_id, self.MEMORY_EVALUATION, ticker=ticker, limit=5)
            if evals:
                eval_text = self._format_evaluations(evals)
                eval_tokens = estimate_tokens(eval_text)
                if tokens + eval_tokens < max_tokens:
                    parts.append(f"📊 Past Evaluations:\n{eval_text}")
                    tokens += eval_tokens

        # Priority 3: Load memories for tickers mentioned in current message
        if user_message and tokens < max_tokens - 1000:
            mentioned_tickers = self._extract_tickers_from_text(user_message, user_id)
            for mentioned_ticker in mentioned_tickers[:3]:  # Max 3 tickers
                if mentioned_ticker in loaded_tickers:
                    continue
                if tokens >= max_tokens - 500:
                    break

                ticker_journals = self.get_journals(user_id, ticker=mentioned_ticker, limit=5)
                if ticker_journals:
                    ticker_text = self._format_journals(ticker_journals)
                    ticker_tokens = estimate_tokens(ticker_text)
                    if tokens + ticker_tokens < max_tokens:
                        parts.append(f"📝 {mentioned_ticker} Related Records:\n{ticker_text}")
                        tokens += ticker_tokens
                        loaded_tickers.add(mentioned_ticker)

        # Priority 4: Load memories for frequently mentioned tickers from recent journals
        if not ticker and tokens < max_tokens - 1000:
            # Find tickers mentioned in recent journals
            recent_journals = self.get_journals(user_id, limit=20)
            ticker_counts = {}
            for j in recent_journals:
                t = j.get('ticker')
                if t and t not in loaded_tickers:
                    ticker_counts[t] = ticker_counts.get(t, 0) + 1

            # Load in order of most frequently mentioned
            sorted_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)
            for mentioned_ticker, count in sorted_tickers[:3]:
                if tokens >= max_tokens - 500:
                    break

                ticker_journals = self.get_journals(user_id, ticker=mentioned_ticker, limit=5)
                if ticker_journals:
                    ticker_text = self._format_journals(ticker_journals)
                    ticker_tokens = estimate_tokens(ticker_text)
                    if tokens + ticker_tokens < max_tokens:
                        parts.append(f"📝 {mentioned_ticker} Related Records ({count} mentions):\n{ticker_text}")
                        tokens += ticker_tokens
                        loaded_tickers.add(mentioned_ticker)

        # Priority 5: Recent general journals (remaining tokens)
        if tokens < max_tokens - 500:
            recent = self.get_journals(user_id, limit=10)
            # Exclude already included tickers
            recent = [j for j in recent if j.get('ticker') not in loaded_tickers]
            if recent:
                recent_text = self._format_journals(recent[:10])
                recent_tokens = estimate_tokens(recent_text)
                if tokens + recent_tokens < max_tokens:
                    parts.append(f"💭 Recent Thoughts:\n{recent_text}")

        return "\n\n".join(parts) if parts else ""

    def _extract_tickers_from_text(self, text: str, user_id: int) -> List[str]:
        """
        Extract tickers/stock codes from text (simple pattern matching)

        Args:
            text: Input text
            user_id: User ID (for matching stock names from past records)

        Returns:
            List[str]: Extracted ticker list
        """
        import re
        tickers = []

        # 1. Korean stock codes (6-digit numbers)
        kr_pattern = r'\b(\d{6})\b'
        kr_matches = re.findall(kr_pattern, text)
        tickers.extend(kr_matches)

        # 2. US tickers (1-5 uppercase letters)
        us_pattern = r'\b([A-Z]{1,5})\b'
        excluded_words = {
            'I', 'A', 'AN', 'THE', 'IN', 'ON', 'AT', 'TO', 'FOR', 'OF',
            'AND', 'OR', 'IS', 'IT', 'AI', 'AM', 'PM', 'VS', 'OK', 'NO',
            'PER', 'PBR', 'ROE', 'ROA', 'EPS', 'BPS', 'PSR', 'PCR',
            'HBM', 'DRAM', 'NAND', 'SSD', 'GPU', 'CPU', 'AP', 'PC',
        }
        us_matches = re.findall(us_pattern, text)
        for t in us_matches:
            if t not in excluded_words and t not in tickers:
                tickers.append(t)

        # 3. Match stock names from user's past records
        try:
            past_journals = self.get_journals(user_id, limit=50)
            known_names = {}
            for j in past_journals:
                ticker = j.get('ticker')
                ticker_name = j.get('ticker_name')
                if ticker and ticker_name:
                    known_names[ticker_name] = ticker

            for name, ticker in known_names.items():
                if name and name in text and ticker not in tickers:
                    tickers.append(ticker)
        except Exception:
            pass

        return tickers

    # =========================================================================
    # Journal-Specific Methods
    # =========================================================================

    def save_journal(
        self,
        user_id: int,
        text: str,
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = 'kr',
        message_id: Optional[int] = None
    ) -> int:
        """
        Save journal (trading diary)

        Args:
            user_id: User ID
            text: Journal text
            ticker: Stock code/ticker
            ticker_name: Stock name
            market_type: Market type
            message_id: Telegram message ID

        Returns:
            int: Created memory ID
        """
        content = {
            'text': text,
            'raw_input': text,
            'recorded_at': datetime.now().isoformat()
        }

        return self.save_memory(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            content=content,
            ticker=ticker,
            ticker_name=ticker_name,
            market_type=market_type,
            importance_score=0.7,  # Journals have high importance by default
            command_source='/journal',
            message_id=message_id
        )

    def get_journals(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve journals

        Args:
            user_id: User ID
            ticker: Stock code/ticker
            limit: Maximum number of results

        Returns:
            List[Dict]: Journal list
        """
        return self.get_memories(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            ticker=ticker,
            limit=limit
        )

    # =========================================================================
    # Compression Methods
    # =========================================================================

    def compress_old_memories(
        self,
        layer1_days: int = 7,
        layer2_days: int = 30
    ) -> Dict[str, int]:
        """
        Compress old memories (for nightly batch processing)

        Args:
            layer1_days: Days threshold for Layer 1 -> Layer 2 transition (default 7 days)
            layer2_days: Days threshold for Layer 2 -> Layer 3 transition (default 30 days)

        Returns:
            Dict[str, int]: Compression statistics {'layer2_count': n, 'layer3_count': n}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        stats = {'layer2_count': 0, 'layer3_count': 0}

        try:
            now = datetime.now()
            layer2_cutoff = (now - timedelta(days=layer1_days)).isoformat()
            layer3_cutoff = (now - timedelta(days=layer2_days)).isoformat()

            # Layer 1 -> Layer 2 (7+ days old)
            cursor.execute("""
                SELECT id, content, ticker, ticker_name
                FROM user_memories
                WHERE compression_layer = 1
                AND created_at < ?
            """, (layer2_cutoff,))

            for row in cursor.fetchall():
                memory_id, content_json, ticker, ticker_name = row
                content = json.loads(content_json) if content_json else {}

                # Generate summary
                summary = self._generate_summary(content, ticker, ticker_name)

                cursor.execute("""
                    UPDATE user_memories
                    SET compression_layer = 2, summary = ?
                    WHERE id = ?
                """, (summary, memory_id))
                stats['layer2_count'] += 1

            # Layer 2 -> Layer 3 (30+ days old)
            cursor.execute("""
                SELECT id, summary, ticker, ticker_name
                FROM user_memories
                WHERE compression_layer = 2
                AND created_at < ?
            """, (layer3_cutoff,))

            for row in cursor.fetchall():
                memory_id, summary, ticker, ticker_name = row

                # Generate one-line compression
                compressed = self._generate_compressed(summary, ticker, ticker_name)

                cursor.execute("""
                    UPDATE user_memories
                    SET compression_layer = 3, summary = ?
                    WHERE id = ?
                """, (compressed, memory_id))
                stats['layer3_count'] += 1

            conn.commit()
            logger.info(f"Memory compression completed: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Failed to compress memories: {e}")
            conn.rollback()
            return stats
        finally:
            conn.close()

    # =========================================================================
    # User Preference Methods
    # =========================================================================

    def get_user_preferences(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve user preference settings"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT user_id, preferred_tone, investment_style, favorite_tickers,
                       total_evaluations, total_journals, created_at, last_active_at
                FROM user_preferences
                WHERE user_id = ?
            """, (user_id,))

            row = cursor.fetchone()
            if row:
                return {
                    'user_id': row[0],
                    'preferred_tone': row[1],
                    'investment_style': row[2],
                    'favorite_tickers': json.loads(row[3]) if row[3] else [],
                    'total_evaluations': row[4],
                    'total_journals': row[5],
                    'created_at': row[6],
                    'last_active_at': row[7]
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get user preferences: {e}")
            return None
        finally:
            conn.close()

    def update_user_preferences(
        self,
        user_id: int,
        preferred_tone: Optional[str] = None,
        investment_style: Optional[str] = None,
        favorite_tickers: Optional[List[str]] = None
    ):
        """Update user preference settings"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()

            # Check existing settings
            cursor.execute("SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone() is not None

            if exists:
                updates = []
                params = []

                if preferred_tone is not None:
                    updates.append("preferred_tone = ?")
                    params.append(preferred_tone)

                if investment_style is not None:
                    updates.append("investment_style = ?")
                    params.append(investment_style)

                if favorite_tickers is not None:
                    updates.append("favorite_tickers = ?")
                    params.append(json.dumps(favorite_tickers, ensure_ascii=False))

                updates.append("last_active_at = ?")
                params.append(now)
                params.append(user_id)

                if updates:
                    cursor.execute(f"""
                        UPDATE user_preferences
                        SET {', '.join(updates)}
                        WHERE user_id = ?
                    """, params)
            else:
                favorite_json = json.dumps(favorite_tickers, ensure_ascii=False) if favorite_tickers else None
                cursor.execute("""
                    INSERT INTO user_preferences (
                        user_id, preferred_tone, investment_style, favorite_tickers,
                        total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                """, (user_id, preferred_tone, investment_style, favorite_json, now, now))

            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update user preferences: {e}")
            conn.rollback()
        finally:
            conn.close()

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    def _update_user_stats(self, user_id: int, memory_type: str):
        """Update user statistics"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()

            # Check existing settings
            cursor.execute("SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone() is not None

            if exists:
                if memory_type == self.MEMORY_JOURNAL:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET total_journals = total_journals + 1, last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
                elif memory_type == self.MEMORY_EVALUATION:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET total_evaluations = total_evaluations + 1, last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
                else:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
            else:
                journals = 1 if memory_type == self.MEMORY_JOURNAL else 0
                evals = 1 if memory_type == self.MEMORY_EVALUATION else 0
                cursor.execute("""
                    INSERT INTO user_preferences (
                        user_id, total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (user_id, evals, journals, now, now))

            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update user stats: {e}")
        finally:
            conn.close()

    def _update_access_time(self, memory_ids: List[int]):
        """Update memory access time"""
        if not memory_ids:
            return

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()
            placeholders = ','.join(['?' for _ in memory_ids])
            cursor.execute(f"""
                UPDATE user_memories
                SET last_accessed_at = ?
                WHERE id IN ({placeholders})
            """, [now] + memory_ids)
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update access time: {e}")
        finally:
            conn.close()

    def _format_journals(self, journals: List[Dict[str, Any]]) -> str:
        """Format journals (including detailed content)"""
        lines = []
        for j in journals:
            created = j.get('created_at', '')[:10]
            content = j.get('content', {})
            text = content.get('text', '')[:500]  # Expanded to 500 chars (was 100)
            ticker = j.get('ticker', '')
            ticker_name = j.get('ticker_name', '')

            # Display ticker and ticker name together
            if ticker and ticker_name:
                lines.append(f"- [{created}] {ticker_name}({ticker}): {text}")
            elif ticker:
                lines.append(f"- [{created}] ({ticker}): {text}")
            else:
                lines.append(f"- [{created}] {text}")

        return '\n'.join(lines)

    def _format_evaluations(self, evals: List[Dict[str, Any]]) -> str:
        """Format evaluations (including detailed content)"""
        lines = []
        for e in evals:
            created = e.get('created_at', '')[:10]
            content = e.get('content', {})

            # Use summary if available, otherwise extract from response
            summary = e.get('summary')
            if not summary:
                response = content.get('response_summary', '')
                summary = response[:300] + '...' if len(response) > 300 else response  # Expand to Expanded to 300 chars

            ticker = e.get('ticker', '')
            ticker_name = e.get('ticker_name', '')
            if ticker_name:
                lines.append(f"- [{created}] {ticker_name}({ticker}): {summary}")
            else:
                lines.append(f"- [{created}] {ticker}: {summary}")

        return '\n'.join(lines)

    def _generate_summary(
        self,
        content: Dict[str, Any],
        ticker: Optional[str],
        ticker_name: Optional[str]
    ) -> str:
        """Generate memory summary (for Layer 2)"""
        text = content.get('text', content.get('response_summary', ''))
        if not text:
            return ''

        # Generate simple summary (rule-based without LLM)
        # Could use LLM in practice, but using rule-based to save costs
        ticker_prefix = f"{ticker}: " if ticker else ""
        summary = text[:150].replace('\n', ' ').strip()

        return f"{ticker_prefix}{summary}"

    def _generate_compressed(
        self,
        summary: Optional[str],
        ticker: Optional[str],
        ticker_name: Optional[str]
    ) -> str:
        """Generate one-line compression (for Layer 3)"""
        if not summary:
            return ''

        # One-line compression (max 50 chars)
        ticker_prefix = f"{ticker} " if ticker else ""
        compressed = summary[:50].replace('\n', ' ').strip()

        return f"{ticker_prefix}{compressed}"

    def delete_memory(self, memory_id: int, user_id: int) -> bool:
        """
        Delete specific memory (with ownership verification)

        Args:
            memory_id: Memory ID
            user_id: User ID (for ownership verification)

        Returns:
            bool: Whether deletion was successful
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                DELETE FROM user_memories
                WHERE id = ? AND user_id = ?
            """, (memory_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete memory: {e}")
            return False
        finally:
            conn.close()

    def get_memory_stats(self, user_id: int) -> Dict[str, Any]:
        """Query user memory statistics"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Count by type
            cursor.execute("""
                SELECT memory_type, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ?
                GROUP BY memory_type
            """, (user_id,))
            type_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Count by compression layer
            cursor.execute("""
                SELECT compression_layer, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ?
                GROUP BY compression_layer
            """, (user_id,))
            layer_counts = {f"layer_{row[0]}": row[1] for row in cursor.fetchall()}

            # Count by ticker
            cursor.execute("""
                SELECT ticker, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ? AND ticker IS NOT NULL
                GROUP BY ticker
                ORDER BY count DESC
                LIMIT 10
            """, (user_id,))
            ticker_counts = {row[0]: row[1] for row in cursor.fetchall()}

            return {
                'by_type': type_counts,
                'by_layer': layer_counts,
                'by_ticker': ticker_counts,
                'total': sum(type_counts.values())
            }
        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")
            return {}
        finally:
            conn.close()

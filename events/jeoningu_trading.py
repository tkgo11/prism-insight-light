#!/usr/bin/env python3
"""
Jeon Ingu Contrarian Trading System - '전인구경제연구소' Analysis & Trading Simulator

This bot monitors the YouTube channel '전인구경제연구소', analyzes content using AI,
and implements contrarian trading strategies (betting against Jeon's predictions).

Workflow:
1. Monitor RSS feed for new videos
2. Extract and transcribe audio using OpenAI Whisper
3. Analyze content with AI to detect market sentiment → Structured JSON output
4. Generate Telegram message and send to channel
5. Execute simulated trading and save to SQLite database
"""

import os
import sys
import json
import logging
import asyncio
import yaml
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from decimal import Decimal

# Third-party imports
import feedparser
import yt_dlp
from openai import OpenAI
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from events.jeoningu_trading_db import JeoninguTradingDB

# Setup directories - script is now in events/ directory
DATA_DIR = Path(__file__).parent  # events/ directory
SECRETS_DIR = Path(__file__).parent.parent  # Parent directory for config files

# Configure logging
log_file = DATA_DIR / f"jeoningu_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)

# Constants
CHANNEL_ID = "UCznImSIaxZR7fdLCICLdgaQ"  # 전인구경제연구소
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
VIDEO_HISTORY_FILE = DATA_DIR / "jeoningu_video_history.json"
AUDIO_FILE = DATA_DIR / "temp_audio.mp3"

# Trading configuration
INITIAL_CAPITAL = 10000000  # 1천만원 초기 자본
DEFAULT_POSITION_SIZE = 1000000  # 100만원 기본 포지션 크기


class JeoninguTrading:
    """Main trading bot class for contrarian strategy based on Jeon Ingu's analysis"""

    def __init__(self, use_telegram: bool = True):
        """
        Initialize trading bot with OpenAI client and database

        Args:
            use_telegram: Whether to send messages to Telegram
        """
        # Load API key from mcp_agent.secrets.yaml
        secrets_file = SECRETS_DIR / "mcp_agent.secrets.yaml"
        if not secrets_file.exists():
            raise FileNotFoundError(
                "mcp_agent.secrets.yaml not found. "
                "Please copy mcp_agent.secrets.yaml.example and configure your API keys."
            )

        with open(secrets_file, 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)

        openai_api_key = secrets.get('openai', {}).get('api_key')
        if not openai_api_key or openai_api_key == "example key":
            raise ValueError(
                "OPENAI_API_KEY not found or not configured in mcp_agent.secrets.yaml. "
                "Please set openai.api_key in the secrets file."
            )

        self.openai_client = OpenAI(api_key=openai_api_key)
        self.db = JeoninguTradingDB()
        self.use_telegram = use_telegram

        # Load Telegram config if enabled
        if self.use_telegram:
            self._load_telegram_config()

        logger.info("JeoninguTrading initialized successfully")

    def _load_telegram_config(self):
        """Load Telegram bot token and channel ID from .env"""
        from dotenv import load_dotenv
        load_dotenv(SECRETS_DIR / ".env")

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

        if not self.telegram_bot_token or not self.telegram_channel_id:
            logger.warning("Telegram not configured - disabling Telegram features")
            self.use_telegram = False
        else:
            logger.info("Telegram configuration loaded")

    def fetch_latest_videos(self) -> List[Dict[str, str]]:
        """
        Fetch latest videos from RSS feed

        Returns:
            List of video dictionaries with id, title, published, link
        """
        logger.info(f"Fetching RSS feed from: {RSS_URL}")

        try:
            feed = feedparser.parse(RSS_URL)
            videos = []

            for entry in feed.entries:
                video = {
                    'id': entry.yt_videoid,
                    'title': entry.title,
                    'published': entry.published,
                    'link': entry.link,
                    'author': entry.author if hasattr(entry, 'author') else 'Unknown'
                }
                videos.append(video)

            logger.info(f"Found {len(videos)} videos in feed")
            return videos

        except Exception as e:
            logger.error(f"Error fetching RSS feed: {e}", exc_info=True)
            return []

    def load_previous_videos(self) -> List[Dict[str, str]]:
        """
        Load previous video list from JSON file

        Returns:
            List of previous video dictionaries
        """
        if not Path(VIDEO_HISTORY_FILE).exists():
            logger.info("No previous video history found")
            return []

        try:
            with open(VIDEO_HISTORY_FILE, 'r', encoding='utf-8') as f:
                videos = json.load(f)
            logger.info(f"Loaded {len(videos)} previous videos")
            return videos
        except Exception as e:
            logger.error(f"Error loading video history: {e}", exc_info=True)
            return []

    def save_video_history(self, videos: List[Dict[str, str]]):
        """
        Save current video list to JSON file

        Args:
            videos: List of video dictionaries
        """
        try:
            with open(VIDEO_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(videos)} videos to history")
        except Exception as e:
            logger.error(f"Error saving video history: {e}", exc_info=True)

    def find_new_videos(self, current_videos: List[Dict], previous_videos: List[Dict]) -> List[Dict]:
        """
        Find new videos by comparing current and previous lists

        Args:
            current_videos: Current video list
            previous_videos: Previous video list

        Returns:
            List of new video dictionaries
        """
        previous_ids = {video['id'] for video in previous_videos}
        new_videos = [video for video in current_videos if video['id'] not in previous_ids]

        logger.info(f"Found {len(new_videos)} new videos")
        return new_videos

    def extract_audio(self, video_url: str) -> Optional[str]:
        """
        Extract audio from YouTube video using yt-dlp

        Args:
            video_url: YouTube video URL

        Returns:
            Path to extracted audio file, or None on failure
        """
        logger.info(f"Extracting audio from: {video_url}")

        # Remove all existing temp_audio files (including intermediates)
        for temp_file in DATA_DIR.glob('temp_audio.*'):
            try:
                temp_file.unlink()
                logger.debug(f"Removed existing file: {temp_file}")
            except Exception as e:
                logger.warning(f"Failed to remove {temp_file}: {e}")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(DATA_DIR / 'temp_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
            'keepvideo': False,  # Delete original file after conversion
            'quiet': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if AUDIO_FILE.exists():
                logger.info("Audio extraction successful")
                return str(AUDIO_FILE)
            else:
                logger.error("Audio file not found after extraction")
                return None

        except Exception as e:
            logger.error(f"Error extracting audio: {e}", exc_info=True)
            return None

    def transcribe_audio(self, audio_file: str) -> Optional[str]:
        """
        Transcribe audio using OpenAI Whisper API
        Handles files larger than 25MB by splitting into chunks

        Args:
            audio_file: Path to audio file

        Returns:
            Transcribed text, or None on failure
        """
        logger.info(f"Transcribing audio file: {audio_file}")

        try:
            # Check file size
            file_size = Path(audio_file).stat().st_size
            max_size = 25 * 1024 * 1024  # 25MB in bytes

            logger.info(f"Audio file size: {file_size / (1024*1024):.2f} MB")

            if file_size <= max_size:
                # File is small enough, transcribe directly
                logger.info("File size is within limit, transcribing directly")
                with open(audio_file, "rb") as f:
                    result = self.openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language="ko"
                    )
                transcript = result.text
                logger.info(f"Transcription successful ({len(transcript)} characters)")
                return transcript

            else:
                # File is too large, split and transcribe in chunks
                logger.info("File exceeds 25MB limit, splitting into chunks")
                return self._transcribe_large_file(audio_file)

        except Exception as e:
            logger.error(f"Error transcribing audio: {e}", exc_info=True)
            return None

    def _transcribe_large_file(self, audio_file: str) -> Optional[str]:
        """
        Split large audio file and transcribe in chunks

        Args:
            audio_file: Path to audio file

        Returns:
            Combined transcript text
        """
        try:
            from pydub import AudioSegment

            # Load audio file
            logger.info("Loading audio file for splitting")
            audio = AudioSegment.from_mp3(audio_file)
            duration_ms = len(audio)
            duration_min = duration_ms / 60000

            logger.info(f"Audio duration: {duration_min:.2f} minutes")

            # Split into 10-minute chunks (adjust as needed)
            chunk_length_ms = 10 * 60 * 1000  # 10 minutes
            chunks = []
            transcripts = []

            for i in range(0, duration_ms, chunk_length_ms):
                chunk = audio[i:i + chunk_length_ms]
                chunk_file = DATA_DIR / f"temp_audio_chunk_{i//chunk_length_ms}.mp3"
                chunk.export(chunk_file, format="mp3")
                chunks.append(chunk_file)
                logger.info(f"Created chunk {len(chunks)}: {chunk_file.name}")

            # Transcribe each chunk
            for idx, chunk_file in enumerate(chunks, 1):
                logger.info(f"Transcribing chunk {idx}/{len(chunks)}")
                try:
                    with open(chunk_file, "rb") as f:
                        result = self.openai_client.audio.transcriptions.create(
                            model="whisper-1",
                            file=f,
                            language="ko"
                        )
                    transcripts.append(result.text)
                    logger.info(f"Chunk {idx} transcribed ({len(result.text)} characters)")
                except Exception as e:
                    logger.error(f"Error transcribing chunk {idx}: {e}")
                    transcripts.append(f"[청크 {idx} 변환 실패]")

            # Combine transcripts
            full_transcript = " ".join(transcripts)
            logger.info(f"Combined transcript: {len(full_transcript)} characters from {len(chunks)} chunks")

            # Cleanup chunk files
            for chunk_file in chunks:
                try:
                    chunk_file.unlink()
                    logger.debug(f"Removed chunk file: {chunk_file}")
                except Exception as e:
                    logger.warning(f"Failed to remove chunk file {chunk_file}: {e}")

            return full_transcript

        except ImportError:
            logger.error("pydub is not installed. Please install it: pip install pydub")
            logger.error("Also ensure ffmpeg is installed for audio processing")
            return None
        except Exception as e:
            logger.error(f"Error splitting/transcribing large file: {e}", exc_info=True)
            return None

    def create_analysis_agent(self, video_info: Dict, transcript: str) -> Agent:
        """
        Create AI agent for content analysis and investment recommendation
        Returns structured JSON output instead of Markdown

        Args:
            video_info: Video metadata dictionary
            transcript: Transcribed text

        Returns:
            Configured Agent instance
        """
        instruction = f"""당신은 유튜브 채널 '전인구경제연구소'의 콘텐츠를 분석하는 역발상 투자 전문가입니다.

## 분석 대상 영상
- 제목: {video_info['title']}
- 게시일: {video_info['published']}
- URL: {video_info['link']}

## 영상 자막 전문
{transcript}

## 분석 과제

당신의 임무는 영상을 분석하고 **구조화된 JSON 형식**으로 결과를 출력하는 것입니다.

### 1단계: 콘텐츠 유형 판별
- 전인구 본인이 직접 출연하여 의견을 제시하는 영상인가?
- 단순 뉴스 요약이나 게스트 인터뷰만 있는 영상은 아닌가?

**판별 결과**: "본인의견" 또는 "스킵" 중 하나

### 2단계: 시장 전망 분석
전인구가 시장에 대해 어떤 기조로 말하고 있는지:
- **상승**: 낙관적 전망, 매수 추천, 긍정적 시그널 강조
- **하락**: 비관적 전망, 매도/관망 추천, 부정적 시그널 강조
- **중립**: 명확한 방향성 없음

### 3단계: 역발상 투자 전략
전인구의 의견과 **반대** 방향으로 베팅:

**상승 기조 → 하락에 베팅 (인버스 ETF)**:
- KODEX 인버스 (114800)
- TIGER 인버스 (252670)
- KODEX 코스닥150 인버스 (251340)

**하락 기조 → 상승에 베팅 (레버리지 ETF)**:
- KODEX 레버리지 (122630)
- TIGER 레버리지 (233740)
- KODEX 코스닥150 레버리지 (233160)

**중립 기조 → 관망**

## 출력 형식 (JSON)

다음 JSON 스키마를 **반드시** 따라 출력하세요:

```json
{{
  "video_info": {{
    "video_id": "{video_info['id']}",
    "title": "{video_info['title']}",
    "published_date": "{video_info['published']}",
    "video_url": "{video_info['link']}",
    "analyzed_date": "{datetime.now().isoformat()}"
  }},
  "content_type": "본인의견" | "스킵",
  "jeon_analysis": {{
    "market_sentiment": "상승" | "하락" | "중립",
    "key_quotes": [
      "자막에서 인용한 핵심 발언 1",
      "자막에서 인용한 핵심 발언 2",
      "자막에서 인용한 핵심 발언 3"
    ],
    "summary": [
      "핵심 논점 1",
      "핵심 논점 2",
      "핵심 논점 3"
    ],
    "mentioned_stocks": [
      {{"code": "005930", "name": "삼성전자"}},
      {{"code": "000660", "name": "SK하이닉스"}}
    ]
  }},
  "contrarian_strategy": {{
    "action": "매수" | "매도" | "관망",
    "reasoning": "역발상 전략의 근거를 2-3문장으로 설명",
    "target_stocks": [
      {{
        "code": "114800",
        "name": "KODEX 인버스",
        "type": "ETF",
        "reason": "전인구의 상승 전망에 반대하여 하락 베팅"
      }}
    ],
    "entry_timing": "즉시 진입" | "조정 대기" | "분할 매수",
    "position_size_pct": 10,
    "confidence_score": 0.75
  }},
  "risk_management": {{
    "stop_loss_pct": -7,
    "target_profit_pct": 15,
    "max_position_pct": 10,
    "warning": "역발상 전략의 리스크 경고 메시지"
  }},
  "telegram_summary": {{
    "title": "📺 전인구 최신 분석 (역발상 관점)",
    "content": "텔레그램 메시지로 보낼 요약 (5-7줄, 이모지 포함)",
    "hashtags": ["#전인구", "#역발상투자", "#인버스ETF"]
  }}
}}
```

## 중요 사항
- **반드시 valid JSON만 출력**하세요 (마크다운 코드블록 없이)
- 자막 내용만을 근거로 분석 (추측 금지)
- confidence_score는 0.0~1.0 사이 값
- 텔레그램 요약은 간결하고 실용적으로 작성
"""

        return Agent(
            name="jeoningu_contrarian_analyst",
            instruction=instruction,
            server_names=[]  # No MCP servers needed for transcript analysis
        )

    async def analyze_video(self, video_info: Dict, transcript: str) -> Optional[Dict]:
        """
        Analyze video content using AI agent
        Returns structured JSON data instead of markdown text

        Args:
            video_info: Video metadata
            transcript: Transcribed text

        Returns:
            Analysis result as dictionary, or None on failure
        """
        logger.info(f"Analyzing video: {video_info['title']}")

        try:
            agent = self.create_analysis_agent(video_info, transcript)

            # Initialize MCPApp context
            app = MCPApp(name="jeoningu_trading_analysis")

            async with app.run() as _:
                # Attach LLM to agent within MCPApp context
                llm = await agent.attach_llm(OpenAIAugmentedLLM)

                # Generate analysis using the agent
                result = await llm.generate_str(
                    message="위 지시사항에 따라 영상을 분석하고 역발상 투자 전략을 JSON 형식으로 출력해주세요.",
                    request_params=RequestParams(
                        model="gpt-5",
                        maxTokens=16000,
                        max_iterations=3,
                        parallel_tool_calls=False,
                        use_history=True
                    )
                )

            # Parse JSON from result
            # Sometimes LLM returns JSON in markdown code block, clean it
            result_clean = result.strip()
            if result_clean.startswith("```json"):
                result_clean = result_clean[7:]
            if result_clean.startswith("```"):
                result_clean = result_clean[3:]
            if result_clean.endswith("```"):
                result_clean = result_clean[:-3]
            result_clean = result_clean.strip()

            analysis_data = json.loads(result_clean)
            logger.info("Analysis completed successfully")

            return analysis_data

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response: {e}")
            logger.error(f"Raw response: {result[:500]}...")
            return None
        except Exception as e:
            logger.error(f"Error during analysis: {e}", exc_info=True)
            return None

    async def send_telegram_message(self, analysis: Dict) -> Optional[int]:
        """
        Send analysis summary to Telegram channel

        Args:
            analysis: Analysis result dictionary

        Returns:
            Message ID if sent successfully, None otherwise
        """
        if not self.use_telegram:
            logger.info("Telegram disabled, skipping message send")
            return None

        try:
            from telegram import Bot

            telegram_data = analysis.get('telegram_summary', {})

            # Build message text
            title = telegram_data.get('title', '📺 전인구 최신 분석')
            content = telegram_data.get('content', '')
            hashtags = ' '.join(telegram_data.get('hashtags', []))

            video_url = analysis['video_info']['video_url']

            message_text = f"""
{title}

{content}

🔗 영상 보기: {video_url}

{hashtags}

⚠️ 본 정보는 투자 권유가 아닌 참고용입니다.
""".strip()

            bot = Bot(token=self.telegram_bot_token)
            message = await bot.send_message(
                chat_id=self.telegram_channel_id,
                text=message_text,
                parse_mode='HTML',
                disable_web_page_preview=False
            )

            logger.info(f"Telegram message sent successfully (message_id: {message.message_id})")
            return message.message_id

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}", exc_info=True)
            return None

    async def execute_simulated_trade(self, analysis: Dict) -> Optional[int]:
        """
        Execute simulated trade based on analysis and save to database

        Args:
            analysis: Analysis result dictionary

        Returns:
            Trade ID if executed, None otherwise
        """
        try:
            contrarian = analysis.get('contrarian_strategy', {})
            action = contrarian.get('action')

            if action == '관망':
                logger.info("Strategy is 관망 (wait), skipping trade")
                return None

            target_stocks = contrarian.get('target_stocks', [])
            if not target_stocks:
                logger.warning("No target stocks specified")
                return None

            # Use first target stock
            stock = target_stocks[0]
            stock_code = stock['code']
            stock_name = stock['name']

            # Get current price (mock for now - integrate with pykrx later)
            # TODO: Fetch real price from pykrx
            current_price = 10000  # Mock price

            # Calculate quantity based on position size
            position_size = DEFAULT_POSITION_SIZE
            quantity = int(position_size / current_price)
            total_amount = quantity * current_price

            video_id = analysis['video_info']['video_id']

            # Save video to database
            await self.db.insert_video({
                'video_id': video_id,
                'title': analysis['video_info']['title'],
                'published_date': analysis['video_info']['published_date'],
                'analyzed_date': analysis['video_info']['analyzed_date'],
                'video_url': analysis['video_info']['video_url'],
                'transcript_summary': ' '.join(analysis['jeon_analysis']['summary'][:3])
            })

            # Save analysis to database
            analysis_id = await self.db.insert_analysis({
                'video_id': video_id,
                'jeon_prediction': analysis['jeon_analysis']['market_sentiment'],
                'jeon_reasoning': ' '.join(analysis['jeon_analysis']['key_quotes'][:2]),
                'contrarian_strategy': action,
                'contrarian_reasoning': contrarian.get('reasoning', ''),
                'target_stocks': target_stocks,
                'confidence_score': contrarian.get('confidence_score', 0.5),
                'raw_analysis': analysis
            })

            # Execute BUY trade
            if action == '매수':
                trade_id = await self.db.insert_trade({
                    'video_id': video_id,
                    'analysis_id': analysis_id,
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'trade_type': 'BUY',
                    'trade_date': datetime.now().isoformat(),
                    'quantity': quantity,
                    'price': current_price,
                    'total_amount': total_amount,
                    'strategy_note': stock.get('reason', '')
                })

                # Add to portfolio
                await self.db.update_portfolio(stock_code, {
                    'stock_name': stock_name,
                    'buy_trade_id': trade_id,
                    'video_id': video_id,
                    'quantity': quantity,
                    'avg_buy_price': current_price,
                    'total_investment': total_amount,
                    'buy_date': datetime.now().isoformat(),
                    'strategy_note': stock.get('reason', '')
                })

                logger.info(f"✅ BUY executed: {stock_name} ({stock_code}) x {quantity} @ {current_price}")
                return trade_id

            # TODO: Implement SELL logic (check portfolio for existing positions)

        except Exception as e:
            logger.error(f"Error executing simulated trade: {e}", exc_info=True)
            return None

    def cleanup_temp_files(self):
        """Remove temporary audio files including chunks"""
        # Remove all temp_audio files (including intermediates and chunks)
        cleaned_files = []

        # Clean up main audio files
        for temp_file in DATA_DIR.glob('temp_audio.*'):
            try:
                temp_file.unlink()
                cleaned_files.append(temp_file.name)
            except Exception as e:
                logger.warning(f"Failed to clean up {temp_file}: {e}")

        # Clean up chunk files
        for chunk_file in DATA_DIR.glob('temp_audio_chunk_*.mp3'):
            try:
                chunk_file.unlink()
                cleaned_files.append(chunk_file.name)
            except Exception as e:
                logger.warning(f"Failed to clean up {chunk_file}: {e}")

        if cleaned_files:
            logger.info(f"Cleaned up temporary audio files: {', '.join(cleaned_files)}")
        else:
            logger.debug("No temporary audio files to clean up")

    async def process_new_video(self, video_info: Dict) -> Optional[Dict]:
        """
        Process a new video: extract audio, transcribe, analyze, send telegram, execute trade

        Args:
            video_info: Video metadata dictionary

        Returns:
            Analysis result dictionary, or None on failure
        """
        logger.info(f"Processing new video: {video_info['title']}")

        try:
            # Step 1: Extract audio
            audio_file = self.extract_audio(video_info['link'])
            if not audio_file:
                return None

            # Step 2: Transcribe audio
            transcript = self.transcribe_audio(audio_file)
            if not transcript:
                return None

            # Save transcript for debugging
            transcript_file = DATA_DIR / f"transcript_{video_info['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(f"Video: {video_info['title']}\n")
                f.write(f"URL: {video_info['link']}\n")
                f.write(f"Published: {video_info['published']}\n")
                f.write(f"\n{'='*80}\n\n")
                f.write(transcript)
            logger.info(f"Transcript saved to: {transcript_file}")

            # Step 3: Analyze content (get structured JSON)
            analysis = await self.analyze_video(video_info, transcript)
            if not analysis:
                logger.error("Analysis failed")
                return None

            # Check if content should be skipped
            if analysis.get('content_type') == '스킵':
                logger.info("Content type is '스킵', skipping further processing")
                return analysis

            # Step 4: Send Telegram message
            message_id = await self.send_telegram_message(analysis)
            if message_id:
                # Record telegram message in DB
                await self.db.insert_telegram_message({
                    'video_id': analysis['video_info']['video_id'],
                    'analysis_id': 0,  # Will be updated after trade execution
                    'message_text': analysis['telegram_summary']['content'],
                    'channel_id': self.telegram_channel_id,
                    'sent_at': datetime.now().isoformat(),
                    'message_id': message_id
                })

            # Step 5: Execute simulated trade
            trade_id = await self.execute_simulated_trade(analysis)
            if trade_id:
                logger.info(f"Trade executed successfully (trade_id: {trade_id})")

            # Step 6: Calculate and log performance metrics
            metrics = await self.db.calculate_performance_metrics()
            logger.info(f"Performance: Win rate {metrics['win_rate']:.1f}%, Cumulative return {metrics['cumulative_return']:.2f}%")

            return analysis

        except Exception as e:
            logger.error(f"Error processing video: {e}", exc_info=True)
            return None

        finally:
            # Always cleanup temporary files
            self.cleanup_temp_files()

    async def process_single_video_url(self, video_url: str):
        """
        Process a single video URL directly (for testing)

        Args:
            video_url: YouTube video URL
        """
        logger.info("="*80)
        logger.info("Jeon Ingu Contrarian Trading - Single Video Mode")
        logger.info("="*80)

        try:
            # Initialize database
            await self.db.initialize()

            # Create video info from URL
            video_info = {
                'title': 'Manual Video Input',
                'published': datetime.now().isoformat(),
                'link': video_url,
                'id': video_url.split('=')[-1] if '=' in video_url else video_url.split('/')[-1]
            }

            logger.info(f"Processing video: {video_url}")

            analysis = await self.process_new_video(video_info)

            if analysis:
                # Print analysis to console
                print("\n" + "="*80)
                print("ANALYSIS RESULT (JSON)")
                print("="*80)
                print(json.dumps(analysis, ensure_ascii=False, indent=2))
                print("="*80 + "\n")
            else:
                logger.warning("Failed to analyze video")

            logger.info("="*80)
            logger.info("Jeon Ingu Contrarian Trading - Completed")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Fatal error processing video: {e}", exc_info=True)
            raise

    async def run(self):
        """Main execution workflow"""
        logger.info("="*80)
        logger.info("Jeon Ingu Contrarian Trading - Starting")
        logger.info("="*80)

        try:
            # Initialize database
            await self.db.initialize()

            # Step 1: Fetch latest videos from RSS
            current_videos = self.fetch_latest_videos()
            if not current_videos:
                logger.warning("No videos found in RSS feed")
                return

            # Step 2: Load previous video history
            previous_videos = self.load_previous_videos()

            # Check if this is first run
            is_first_run = len(previous_videos) == 0

            if is_first_run:
                logger.info("🎬 First run detected - initializing video history")
                logger.info(f"Found {len(current_videos)} videos in channel")
                logger.info("Saving video history without processing...")

                # Save current videos and exit
                self.save_video_history(current_videos)

                logger.info("="*80)
                logger.info("✅ Video history initialized successfully")
                logger.info("💡 Run again to detect and process new videos")
                logger.info("="*80)
                return

            # Step 3: Find new videos
            new_videos = self.find_new_videos(current_videos, previous_videos)

            if not new_videos:
                logger.info("No new videos found")
                return

            # Step 4: Process each new video
            for video in new_videos:
                logger.info("\n" + "="*80)
                logger.info(f"Processing: {video['title']}")
                logger.info("="*80)

                analysis = await self.process_new_video(video)

                if analysis:
                    # Print analysis summary to console
                    print("\n" + "="*80)
                    print("ANALYSIS RESULT (JSON)")
                    print("="*80)
                    print(json.dumps(analysis, ensure_ascii=False, indent=2))
                    print("="*80 + "\n")
                else:
                    logger.warning(f"Failed to analyze video: {video['title']}")

            # Step 5: Save updated video history
            self.save_video_history(current_videos)

            logger.info("="*80)
            logger.info("Jeon Ingu Contrarian Trading - Completed")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Fatal error in main workflow: {e}", exc_info=True)
            raise


async def main():
    """Entry point"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Jeon Ingu Contrarian Trading - 전인구경제연구소 역발상 투자 분석 및 시뮬레이션",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal mode (monitor RSS feed for new videos)
  python events/jeoningu_trading.py

  # Test mode (process specific video URL)
  python events/jeoningu_trading.py --video-url "https://www.youtube.com/watch?v=VIDEO_ID"

  # Disable Telegram
  python events/jeoningu_trading.py --no-telegram
        """
    )
    parser.add_argument(
        '--video-url',
        type=str,
        help='Process a specific YouTube video URL (test mode)'
    )
    parser.add_argument(
        '--no-telegram',
        action='store_true',
        help='Disable Telegram message sending'
    )

    args = parser.parse_args()

    try:
        bot = JeoninguTrading(use_telegram=not args.no_telegram)

        if args.video_url:
            # Single video mode
            logger.info(f"🎯 Test mode: Processing single video")
            await bot.process_single_video_url(args.video_url)
        else:
            # Normal RSS monitoring mode
            await bot.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

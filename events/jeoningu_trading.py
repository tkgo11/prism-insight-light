#!/usr/bin/env python3
"""
Jeon Ingu Contrarian Trading System - '전인구경제연구소' Analysis & Trading Simulator

Simplified strategy:
- Jeon says UP → Buy KODEX Inverse (114800)
- Jeon says NEUTRAL → Sell all positions
- Jeon says DOWN → Buy KODEX 200 (069500)

Always hold max 1 position at a time. Switch positions when sentiment changes.
"""

import os
import sys
import json
import logging
import asyncio
import yaml
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

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

# Setup directories
DATA_DIR = Path(__file__).parent
SECRETS_DIR = Path(__file__).parent.parent

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
POSITION_SIZE = 1000000  # 100만원 고정 포지션

# Stock codes
KODEX_200 = "069500"
KODEX_INVERSE = "114800"


class JeoninguTrading:
    """Main trading bot for contrarian strategy"""

    def __init__(self, use_telegram: bool = True):
        """Initialize bot"""
        # Load OpenAI API key
        secrets_file = SECRETS_DIR / "mcp_agent.secrets.yaml"
        if not secrets_file.exists():
            raise FileNotFoundError("mcp_agent.secrets.yaml not found")

        with open(secrets_file, 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)

        openai_api_key = secrets.get('openai', {}).get('api_key')
        if not openai_api_key or openai_api_key == "example key":
            raise ValueError("OPENAI_API_KEY not configured in mcp_agent.secrets.yaml")

        self.openai_client = OpenAI(api_key=openai_api_key)
        self.db = JeoninguTradingDB()
        self.use_telegram = use_telegram

        # Load Telegram config if enabled
        if self.use_telegram:
            self._load_telegram_config()

        logger.info("JeoninguTrading initialized")

    def _load_telegram_config(self):
        """Load Telegram credentials"""
        from dotenv import load_dotenv
        load_dotenv(SECRETS_DIR / ".env")

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

        if not self.telegram_bot_token or not self.telegram_channel_id:
            logger.warning("Telegram not configured - disabling")
            self.use_telegram = False

    def fetch_latest_videos(self) -> List[Dict[str, str]]:
        """Fetch videos from RSS feed"""
        logger.info(f"Fetching RSS: {RSS_URL}")
        try:
            feed = feedparser.parse(RSS_URL)
            videos = []
            for entry in feed.entries:
                videos.append({
                    'id': entry.yt_videoid,
                    'title': entry.title,
                    'published': entry.published,
                    'link': entry.link,
                    'author': entry.author if hasattr(entry, 'author') else 'Unknown'
                })
            logger.info(f"Found {len(videos)} videos")
            return videos
        except Exception as e:
            logger.error(f"RSS fetch error: {e}", exc_info=True)
            return []

    def load_previous_videos(self) -> List[Dict[str, str]]:
        """Load video history"""
        if not VIDEO_HISTORY_FILE.exists():
            return []
        try:
            with open(VIDEO_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def save_video_history(self, videos: List[Dict[str, str]]):
        """Save video history"""
        try:
            with open(VIDEO_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(videos)} videos")
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def find_new_videos(self, current: List[Dict], previous: List[Dict]) -> List[Dict]:
        """Find new videos"""
        previous_ids = {v['id'] for v in previous}
        new_videos = [v for v in current if v['id'] not in previous_ids]
        logger.info(f"Found {len(new_videos)} new videos")
        return new_videos

    def extract_audio(self, video_url: str) -> Optional[str]:
        """Extract audio from YouTube"""
        logger.info(f"Extracting audio: {video_url}")

        # Clean up old files
        for temp_file in DATA_DIR.glob('temp_audio.*'):
            try:
                temp_file.unlink()
            except Exception:
                pass

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(DATA_DIR / 'temp_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
            'keepvideo': False,
            'quiet': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if AUDIO_FILE.exists():
                logger.info("Audio extraction successful")
                return str(AUDIO_FILE)
            return None
        except Exception as e:
            logger.error(f"Audio extraction error: {e}")
            return None

    def transcribe_audio(self, audio_file: str) -> Optional[str]:
        """Transcribe audio with Whisper"""
        logger.info(f"Transcribing: {audio_file}")

        try:
            file_size = Path(audio_file).stat().st_size
            max_size = 25 * 1024 * 1024  # 25MB

            if file_size <= max_size:
                with open(audio_file, "rb") as f:
                    result = self.openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language="ko"
                    )
                logger.info(f"Transcription done ({len(result.text)} chars)")
                return result.text
            else:
                # Split large files
                return self._transcribe_large_file(audio_file)

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None

    def _transcribe_large_file(self, audio_file: str) -> Optional[str]:
        """Split and transcribe large audio files"""
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_mp3(audio_file)
            chunk_length_ms = 10 * 60 * 1000  # 10 minutes
            chunks = []
            transcripts = []

            for i in range(0, len(audio), chunk_length_ms):
                chunk = audio[i:i + chunk_length_ms]
                chunk_file = DATA_DIR / f"temp_audio_chunk_{i//chunk_length_ms}.mp3"
                chunk.export(chunk_file, format="mp3")
                chunks.append(chunk_file)

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
                except Exception as e:
                    logger.error(f"Chunk {idx} error: {e}")
                    transcripts.append(f"[Chunk {idx} failed]")

            # Cleanup
            for chunk_file in chunks:
                try:
                    chunk_file.unlink()
                except Exception:
                    pass

            return " ".join(transcripts)

        except ImportError:
            logger.error("pydub not installed. Install: pip install pydub")
            return None
        except Exception as e:
            logger.error(f"Large file transcription error: {e}")
            return None

    def create_analysis_agent(self, video_info: Dict, transcript: str) -> Agent:
        """
        Create AI agent for analysis

        Simplified strategy:
        - Jeon UP → Inverse (114800)
        - Jeon NEUTRAL → Sell all
        - Jeon DOWN → KODEX 200 (069500)
        """
        instruction = f"""당신은 전인구경제연구소 콘텐츠를 분석하는 역발상 투자 전문가입니다.

## 영상 정보
- 제목: {video_info['title']}
- 게시일: {video_info['published']}
- URL: {video_info['link']}

## 영상 자막
{transcript}

## 분석 과제

### 1단계: 콘텐츠 유형 판별
전인구 본인이 직접 출연하여 시장 의견을 제시하는 영상인가?
- "본인의견": 전인구가 직접 시장 전망 언급
- "스킵": 단순 뉴스 요약, 게스트 인터뷰만 있는 경우

### 2단계: 시장 기조 분석
전인구가 시장에 대해 어떤 기조로 말하는지 판단:
- "상승": 낙관적 전망, 매수 추천, 긍정적 시그널 강조
- "하락": 비관적 전망, 매도/관망 추천, 부정적 시그널 강조
- "중립": 명확한 방향성 없음, 애매한 의견

### 3단계: 역발상 전략 결정

**투자 종목 (2개만 사용)**:
- KODEX 200 (069500): 코스피 200 지수 추종
- KODEX 인버스 (114800): 코스피 200 반대 방향

**전략 규칙**:
1. 전인구 **상승** 기조 → 반대로 **하락**에 베팅 → **KODEX 인버스(114800) 매수**
2. 전인구 **중립** 기조 → 관망 → **보유 종목 전량 매도 (현금화)**
3. 전인구 **하락** 기조 → 반대로 **상승**에 베팅 → **KODEX 200(069500) 매수**

**포지션 관리**:
- 항상 1개 종목만 보유 (069500 또는 114800)
- 다른 종목으로 전환 시: 기존 보유 종목 매도 → 새 종목 매수
- 중립일 때: 보유 종목 있으면 무조건 매도

## 출력 형식 (JSON)

반드시 아래 JSON 스키마를 따라 출력하세요 (마크다운 코드블록 없이 순수 JSON만):

```json
{{
  "video_info": {{
    "video_id": "{video_info['id']}",
    "title": "{video_info['title']}",
    "video_date": "{video_info['published']}",
    "video_url": "{video_info['link']}"
  }},
  "content_type": "본인의견" | "스킵",
  "jeon_sentiment": "상승" | "하락" | "중립",
  "jeon_reasoning": "전인구의 핵심 발언을 2-3개 문장으로 요약",
  "contrarian_action": "인버스매수" | "KODEX매수" | "전량매도",
  "target_stock": {{
    "code": "114800" | "069500" | null,
    "name": "KODEX 인버스" | "KODEX 200" | null
  }},
  "telegram_summary": "텔레그램 메시지 내용 (5줄 이내, 이모지 포함)"
}}
```

## 중요 사항
- **반드시 valid JSON만 출력** (마크다운 코드블록 제거)
- 자막 내용만 근거로 분석 (추측 금지)
- 종목은 069500, 114800 중 하나만 선택
- 중립일 때는 target_stock을 null로 설정
"""

        return Agent(
            name="jeoningu_analyst",
            instruction=instruction,
            server_names=[]
        )

    async def analyze_video(self, video_info: Dict, transcript: str) -> Optional[Dict]:
        """Analyze video and return structured JSON"""
        logger.info(f"Analyzing: {video_info['title']}")

        try:
            agent = self.create_analysis_agent(video_info, transcript)
            app = MCPApp(name="jeoningu_analysis")

            async with app.run() as _:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                result = await llm.generate_str(
                    message="위 지시사항에 따라 영상을 분석하고 역발상 투자 전략을 JSON 형식으로 출력해주세요.",
                    request_params=RequestParams(
                        model="gpt-5",
                        maxTokens=8000,
                        max_iterations=3,
                        parallel_tool_calls=False,
                        use_history=True
                    )
                )

            # Clean JSON response
            result_clean = result.strip()
            if result_clean.startswith("```json"):
                result_clean = result_clean[7:]
            if result_clean.startswith("```"):
                result_clean = result_clean[3:]
            if result_clean.endswith("```"):
                result_clean = result_clean[:-3]
            result_clean = result_clean.strip()

            analysis = json.loads(result_clean)
            logger.info("Analysis completed")
            return analysis

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response: {result[:500]}")
            return None
        except Exception as e:
            logger.error(f"Analysis error: {e}", exc_info=True)
            return None

    async def send_telegram_message(self, analysis: Dict) -> Optional[int]:
        """Send message to Telegram"""
        if not self.use_telegram:
            return None

        try:
            from telegram import Bot

            summary = analysis.get('telegram_summary', '')
            video_url = analysis['video_info']['video_url']
            sentiment = analysis.get('jeon_sentiment', '알 수 없음')
            action = analysis.get('contrarian_action', '관망')

            message_text = f"""
📺 전인구 최신 분석 (역발상 관점)

{summary}

📊 전인구 기조: {sentiment}
💡 역발상 액션: {action}

🔗 영상: {video_url}

⚠️ 투자 권유 아님. 참고용 정보입니다.
""".strip()

            bot = Bot(token=self.telegram_bot_token)
            message = await bot.send_message(
                chat_id=self.telegram_channel_id,
                text=message_text,
                parse_mode='HTML',
                disable_web_page_preview=False
            )

            logger.info(f"Telegram sent (message_id: {message.message_id})")
            return message.message_id

        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return None

    async def execute_trading_strategy(self, analysis: Dict):
        """
        Execute trading strategy based on analysis

        Strategy:
        - UP → Buy Inverse (114800)
        - NEUTRAL → Sell all
        - DOWN → Buy KODEX 200 (069500)
        """
        try:
            sentiment = analysis.get('jeon_sentiment')
            action = analysis.get('contrarian_action')
            target_stock = analysis.get('target_stock', {})

            # Get current position
            current_position = await self.db.get_current_position()
            current_balance = await self.db.get_latest_balance()

            # Initialize balance if first trade
            if current_balance == 0:
                current_balance = INITIAL_CAPITAL

            video_info = analysis['video_info']
            analyzed_date = datetime.now().isoformat()

            # Determine what to do
            trades_executed = []

            # Case 1: NEUTRAL → Sell all positions
            if sentiment == '중립':
                if current_position:
                    # Sell current position
                    # TODO: Get current price from pykrx (mock for now)
                    sell_price = 10500  # Mock
                    sell_amount = current_position['quantity'] * sell_price
                    profit_loss = sell_amount - current_position['buy_amount']
                    profit_loss_pct = (profit_loss / current_position['buy_amount']) * 100

                    new_balance = current_balance + profit_loss
                    cumulative_return_pct = ((new_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

                    sell_trade = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'SELL',
                        'stock_code': current_position['stock_code'],
                        'stock_name': current_position['stock_name'],
                        'quantity': current_position['quantity'],
                        'price': sell_price,
                        'amount': sell_amount,
                        'profit_loss': profit_loss,
                        'profit_loss_pct': profit_loss_pct,
                        'cumulative_balance': new_balance,
                        'cumulative_return_pct': cumulative_return_pct,
                        'notes': f"중립 기조로 전량 매도 (손익: {profit_loss:,.0f}원, {profit_loss_pct:+.2f}%)"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    logger.info(f"✅ SELL: {current_position['stock_name']} (중립 기조)")
                else:
                    # No position to sell, just record analysis
                    record = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'cumulative_balance': current_balance,
                        'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                        'notes': '중립 기조, 보유 종목 없음'
                    }
                    await self.db.insert_trade(record)
                    logger.info("중립 기조, 보유 종목 없음")

            # Case 2: UP or DOWN → Buy target stock
            elif sentiment in ['상승', '하락']:
                target_code = target_stock.get('code')
                target_name = target_stock.get('name')

                if not target_code:
                    logger.warning(f"No target stock for sentiment: {sentiment}")
                    return

                # Step 1: Sell current position if different stock
                if current_position and current_position['stock_code'] != target_code:
                    # Sell different stock
                    sell_price = 10500  # Mock
                    sell_amount = current_position['quantity'] * sell_price
                    profit_loss = sell_amount - current_position['buy_amount']
                    profit_loss_pct = (profit_loss / current_position['buy_amount']) * 100

                    new_balance = current_balance + profit_loss
                    cumulative_return_pct = ((new_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

                    sell_trade = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'SELL',
                        'stock_code': current_position['stock_code'],
                        'stock_name': current_position['stock_name'],
                        'quantity': current_position['quantity'],
                        'price': sell_price,
                        'amount': sell_amount,
                        'profit_loss': profit_loss,
                        'profit_loss_pct': profit_loss_pct,
                        'cumulative_balance': new_balance,
                        'cumulative_return_pct': cumulative_return_pct,
                        'notes': f"종목 전환을 위한 매도 → {target_name} 매수 예정"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    current_balance = new_balance
                    logger.info(f"✅ SELL: {current_position['stock_name']} (종목 전환)")

                elif current_position and current_position['stock_code'] == target_code:
                    # Already holding target stock, no action needed
                    record = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'cumulative_balance': current_balance,
                        'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                        'notes': f'이미 {target_name} 보유 중, 액션 없음'
                    }
                    await self.db.insert_trade(record)
                    logger.info(f"이미 {target_name} 보유 중")
                    return

                # Step 2: Buy target stock
                buy_price = 10000  # Mock
                quantity = int(POSITION_SIZE / buy_price)
                buy_amount = quantity * buy_price

                buy_trade = {
                    'video_id': video_info['video_id'],
                    'video_title': video_info['title'],
                    'video_date': video_info['video_date'],
                    'video_url': video_info['video_url'],
                    'analyzed_date': analyzed_date,
                    'jeon_sentiment': sentiment,
                    'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                    'contrarian_action': action,
                    'trade_type': 'BUY',
                    'stock_code': target_code,
                    'stock_name': target_name,
                    'quantity': quantity,
                    'price': buy_price,
                    'amount': buy_amount,
                    'cumulative_balance': current_balance,
                    'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                    'notes': f"{sentiment} 기조 → 역발상 {target_name} 매수"
                }
                await self.db.insert_trade(buy_trade)
                trades_executed.append(buy_trade)
                logger.info(f"✅ BUY: {target_name} x {quantity} @ {buy_price}")

            # Log performance metrics
            metrics = await self.db.calculate_performance_metrics()
            logger.info(f"📊 Performance: Win {metrics['win_rate']:.1f}%, Return {metrics['cumulative_return']:.2f}%")

        except Exception as e:
            logger.error(f"Trading execution error: {e}", exc_info=True)

    def cleanup_temp_files(self):
        """Cleanup temporary audio files"""
        for temp_file in DATA_DIR.glob('temp_audio*'):
            try:
                temp_file.unlink()
            except Exception:
                pass

    async def process_new_video(self, video_info: Dict) -> Optional[Dict]:
        """Process new video: extract, transcribe, analyze, trade"""
        logger.info(f"Processing: {video_info['title']}")

        try:
            # Extract audio
            audio_file = self.extract_audio(video_info['link'])
            if not audio_file:
                return None

            # Transcribe
            transcript = self.transcribe_audio(audio_file)
            if not transcript:
                return None

            # Save transcript
            transcript_file = DATA_DIR / f"transcript_{video_info['id']}.txt"
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(f"Video: {video_info['title']}\n")
                f.write(f"URL: {video_info['link']}\n\n")
                f.write(transcript)

            # Analyze
            analysis = await self.analyze_video(video_info, transcript)
            if not analysis:
                return None

            # Skip if not Jeon's own opinion
            if analysis.get('content_type') == '스킵':
                logger.info("Content type '스킵', skipping")
                return analysis

            # Send Telegram
            await self.send_telegram_message(analysis)

            # Execute trading
            await self.execute_trading_strategy(analysis)

            return analysis

        except Exception as e:
            logger.error(f"Video processing error: {e}", exc_info=True)
            return None
        finally:
            self.cleanup_temp_files()

    async def process_single_video_url(self, video_url: str):
        """Test mode: process single video"""
        logger.info("="*80)
        logger.info("Single Video Mode")
        logger.info("="*80)

        try:
            await self.db.initialize()

            video_info = {
                'title': 'Test Video',
                'published': datetime.now().isoformat(),
                'link': video_url,
                'id': video_url.split('=')[-1] if '=' in video_url else video_url.split('/')[-1]
            }

            analysis = await self.process_new_video(video_info)

            if analysis:
                print("\n" + "="*80)
                print("ANALYSIS RESULT")
                print("="*80)
                print(json.dumps(analysis, ensure_ascii=False, indent=2))
                print("="*80 + "\n")

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise

    async def run(self):
        """Main workflow"""
        logger.info("="*80)
        logger.info("Jeon Ingu Contrarian Trading - Starting")
        logger.info("="*80)

        try:
            await self.db.initialize()

            # Fetch videos
            current_videos = self.fetch_latest_videos()
            if not current_videos:
                logger.warning("No videos found")
                return

            # Load history
            previous_videos = self.load_previous_videos()

            # First run check
            if len(previous_videos) == 0:
                logger.info("🎬 First run - initializing history")
                self.save_video_history(current_videos)
                logger.info("✅ History initialized. Run again to process new videos.")
                return

            # Find new videos
            new_videos = self.find_new_videos(current_videos, previous_videos)
            if not new_videos:
                logger.info("No new videos")
                return

            # Process each new video
            for video in new_videos:
                analysis = await self.process_new_video(video)
                if analysis:
                    print(json.dumps(analysis, ensure_ascii=False, indent=2))

            # Save history
            self.save_video_history(current_videos)

            logger.info("="*80)
            logger.info("Completed")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise


async def main():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description="Jeon Ingu Contrarian Trading Simulator"
    )
    parser.add_argument('--video-url', type=str, help='Test mode: process specific video URL')
    parser.add_argument('--no-telegram', action='store_true', help='Disable Telegram')
    args = parser.parse_args()

    try:
        bot = JeoninguTrading(use_telegram=not args.no_telegram)

        if args.video_url:
            await bot.process_single_video_url(args.video_url)
        else:
            await bot.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

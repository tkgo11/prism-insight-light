import asyncio
import re
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import (
    EvaluatorOptimizerLLM,
    QualityRating,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create MCPApp instance
app = MCPApp(name="telegram_summary")

class TelegramSummaryGenerator:
    """
    Class for generating Telegram message summaries from report files
    """

    def __init__(self):
        """Constructor"""
        pass

    async def read_report(self, report_path):
        """
        Read report file
        """
        try:
            with open(report_path, 'r', encoding='utf-8') as file:
                content = file.read()
            return content
        except Exception as e:
            logger.error(f"Failed to read report file: {e}")
            raise

    def extract_metadata_from_filename(self, filename):
        """
        Extract ticker code, company name, date etc. from filename
        """
        pattern = r'(\w+)_(.+)_(\d{8})_.*\.pdf'
        match = re.match(pattern, filename)

        if match:
            stock_code = match.group(1)
            stock_name = match.group(2)
            date_str = match.group(3)

            # Convert YYYYMMDD format to YYYY.MM.DD format
            formatted_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "date": formatted_date
            }
        else:
            # If unable to extract info from filename, use defaults
            return {
                "stock_code": "N/A",
                "stock_name": Path(filename).stem,
                "date": datetime.now().strftime("%Y.%m.%d")
            }

    def determine_trigger_type(self, stock_code: str, report_date=None):
        """
        Determine trigger type for the stock from trigger result files

        Logic:
        1. Check both morning and afternoon mode trigger result files
        2. If both exist, prioritize afternoon (most recent data)
        3. If only one exists, select that mode
        4. If neither exists, return default value

        This considers the daily schedule execution order (morning → afternoon)
        to utilize the most recent market data.

        Args:
            stock_code: Stock code
            report_date: Report date (YYYYMMDD)

        Returns:
            tuple: (trigger type, trigger mode)
        """
        logger.info(f"Starting trigger type determination for stock {stock_code}")

        # Use current date if not provided
        if report_date is None:
            report_date = datetime.now().strftime("%Y%m%d")
        elif report_date and "." in report_date:
            # Convert YYYY.MM.DD format to YYYYMMDD
            report_date = report_date.replace(".", "")

        # Store found trigger info by mode
        found_triggers = {}  # {mode: (trigger_type, stocks)}

        # Check all possible modes (morning, afternoon)
        for mode in ["morning", "afternoon"]:
            # Trigger result file path
            results_file = f"trigger_results_{mode}_{report_date}.json"

            logger.info(f"Checking trigger result file: {results_file}")

            if os.path.exists(results_file):
                try:
                    with open(results_file, 'r', encoding='utf-8') as f:
                        results = json.load(f)

                    # Check all trigger results (exclude metadata)
                    for trigger_type, stocks in results.items():
                        if trigger_type != "metadata":
                            # Check each trigger type
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    if stock.get("code") == stock_code:
                                        # Trigger found in this mode
                                        found_triggers[mode] = (trigger_type, mode)
                                        logger.info(f"Trigger found for stock {stock_code} - Type: {trigger_type}, Mode: {mode}")
                                        break

                        # No need to check next trigger_type if already found
                        if mode in found_triggers:
                            break

                except Exception as e:
                    logger.error(f"Error reading trigger result file: {e}")

        # Return result by priority: afternoon > morning
        if "afternoon" in found_triggers:
            trigger_type, mode = found_triggers["afternoon"]
            logger.info(f"Final selection: afternoon mode - Trigger type: {trigger_type}")
            return trigger_type, mode
        elif "morning" in found_triggers:
            trigger_type, mode = found_triggers["morning"]
            logger.info(f"Final selection: morning mode - Trigger type: {trigger_type}")
            return trigger_type, mode

        # Return default if trigger type not found
        logger.warning(f"Trigger type not found in result files for stock {stock_code}, using default")
        return "Notable Pattern", "unknown"

    def create_optimizer_agent(self, metadata, current_date, from_lang="ko", to_lang="ko"):
        """
        Create Telegram summary generation agent

        Args:
            metadata: Stock metadata
            current_date: Current date (YYYY.MM.DD)
            from_lang: Report source language (default: "ko")
            to_lang: Summary target language (default: "ko")
        """
        from cores.agents.telegram_summary_optimizer_agent import create_telegram_summary_optimizer_agent

        return create_telegram_summary_optimizer_agent(
            metadata=metadata,
            current_date=current_date,
            from_lang=from_lang,
            to_lang=to_lang
        )

    def create_evaluator_agent(self, current_date, from_lang="ko", to_lang="ko"):
        """
        Create Telegram summary evaluation agent

        Args:
            current_date: Current date (YYYY.MM.DD)
            from_lang: Report source language (default: "ko")
            to_lang: Summary target language (default: "ko")
        """
        from cores.agents.telegram_summary_evaluator_agent import create_telegram_summary_evaluator_agent

        return create_telegram_summary_evaluator_agent(
            current_date=current_date,
            from_lang=from_lang,
            to_lang=to_lang
        )

    async def generate_telegram_message(self, report_content, metadata, trigger_type, from_lang="ko", to_lang="ko"):
        """
        Generate Telegram message (with evaluation and optimization)

        Args:
            report_content: Report content
            metadata: Stock metadata
            trigger_type: Trigger type
            from_lang: Report source language (default: "ko")
            to_lang: Summary target language (default: "ko")
        """
        # Set current date (YYYY.MM.DD format)
        current_date = datetime.now().strftime("%Y.%m.%d")

        # Create optimizer agent
        optimizer = self.create_optimizer_agent(metadata, current_date, from_lang, to_lang)

        # Create evaluator agent
        evaluator = self.create_evaluator_agent(current_date, from_lang, to_lang)

        # Configure evaluation-optimization workflow
        evaluator_optimizer = EvaluatorOptimizerLLM(
            optimizer=optimizer,
            evaluator=evaluator,
            llm_factory=OpenAIAugmentedLLM,
            min_rating=QualityRating.EXCELLENT
        )

        # Construct message prompt
        prompt_message = f"""다음은 {metadata['stock_name']}({metadata['stock_code']}) 종목에 대한 상세 분석 보고서입니다.
            이 종목은 {trigger_type} 트리거에 포착되었습니다.

            보고서 내용:
            {report_content}
            """

        # Add warning message if trigger mode is morning
        if metadata.get('trigger_mode') == 'morning':
            logger.info("Adding warning message for 10-minute post-market-open data")
            prompt_message += "\n⚠️ 주의: 본 정보는 장 시작 후 10분 시점 데이터입니다. 현재 상황과 다를 수 있습니다."

        # Generate Telegram message using evaluation-optimization workflow
        response = await evaluator_optimizer.generate_str(
            message=prompt_message,
            request_params=RequestParams(
                model="gpt-5.2",
                reasoning_effort="none",
                maxTokens=6000,
                max_iterations=2
            )
        )

        # Process response - improved method
        logger.info(f"Response type: {type(response)}")

        # If response is string (most ideal case)
        if isinstance(response, str):
            logger.info("Response is in string format.")
            # Check if already in message format
            if response.startswith(('📊', '📈', '📉', '💰', '⚠️', '🔍')):
                return response

            # Find and remove Python object representations
            cleaned_response = re.sub(r'[A-Za-z]+\([^)]*\)', '', response)

            # Try to extract only actual message content
            emoji_start = re.search(r'(📊|📈|📉|💰|⚠️|🔍)', cleaned_response)
            message_end = re.search(r'본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.', cleaned_response)

            if emoji_start and message_end:
                return cleaned_response[emoji_start.start():message_end.end()]

        # If OpenAI API response object (has content attribute)
        if hasattr(response, 'content') and response.content is not None:
            logger.info("Response has content attribute.")
            return response.content

        # ChatCompletionMessage case - has tool_calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.info("Response has tool_calls.")

            # Ignore tool_calls info, return function_call result if exists
            if hasattr(response, 'function_call') and response.function_call:
                logger.info("Response has function_call result.")
                return f"Function call result: {response.function_call}"

            # Only generate text format response for subsequent processing
            # Actual tool_calls processing needs separate logic
            return "Cannot extract text from tool call result. Contact administrator."

        # Last attempt: convert to string and extract message format with regex
        response_str = str(response)
        logger.debug(f"Response string before regex: {response_str[:100]}...")

        # Try to extract Telegram message format with regex
        content_match = re.search(r'(📊|📈|📉|💰|⚠️|🔍).*?본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.', response_str, re.DOTALL)

        if content_match:
            logger.info("Extracted message content with regex.")
            return content_match.group(0)

        # If regex also fails, return default message
        logger.warning("Cannot extract valid Telegram message from response.")
        logger.warning(f"Original message not extracted by regex: {response_str[:100]}...")

        # Generate default message
        default_message = f"""📊 {metadata['stock_name']}({metadata['stock_code']}) - 분석 요약

    1. 현재가: (정보 없음)
    2. 최근 추세: (정보 없음)
    3. 주요 체크포인트: 상세 분석 보고서 참조.

    ⚠️ 자동 생성 오류로 상세 정보를 표시할 수 없습니다. 전체 보고서를 확인하세요.
    본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."""

        return default_message

    def save_telegram_message(self, message, output_path):
        """
        Save generated Telegram message to file
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as file:
                file.write(message)
            logger.info(f"Telegram message saved to {output_path}.")
        except Exception as e:
            logger.error(f"Failed to save Telegram message: {e}")
            raise

    async def process_report(self, report_pdf_path, output_dir="telegram_messages", from_lang="ko", to_lang="ko"):
        """
        Process report file to generate Telegram summary message

        Args:
            report_pdf_path: Report file path
            output_dir: Output directory
            from_lang: Report source language (default: "ko")
            to_lang: Summary target language (default: "ko")
        """
        try:
            # Create output directory
            os.makedirs(output_dir, exist_ok=True)

            # Extract metadata from filename
            filename = os.path.basename(report_pdf_path)
            metadata = self.extract_metadata_from_filename(filename)

            logger.info(f"Processing: {filename} - {metadata['stock_name']}({metadata['stock_code']})")

            # Read report content
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(report_pdf_path)

            # Determine trigger type and mode
            trigger_type, trigger_mode = self.determine_trigger_type(
                metadata['stock_code'],
                metadata.get('date', '').replace('.', '')  # YYYY.MM.DD → YYYYMMDD
            )
            logger.info(f"Detected trigger type: {trigger_type}, mode: {trigger_mode}")

            # Add trigger mode to metadata
            metadata['trigger_mode'] = trigger_mode

            # Generate Telegram summary message
            telegram_message = await self.generate_telegram_message(
                report_content, metadata, trigger_type, from_lang, to_lang
            )

            # Create output file path
            output_file = os.path.join(output_dir, f"{metadata['stock_code']}_{metadata['stock_name']}_telegram.txt")

            # Save message
            self.save_telegram_message(telegram_message, output_file)

            logger.info(f"Telegram message generation complete: {output_file}")

            return telegram_message

        except Exception as e:
            logger.error(f"Error processing report: {e}")
            raise

async def process_all_reports(reports_dir="pdf_reports", output_dir="telegram_messages", date_filter=None, from_lang="ko", to_lang="ko"):
    """
    Process all report files in specified directory

    Args:
        reports_dir: Report directory
        output_dir: Output directory
        date_filter: Date filter
        from_lang: Report source language (default: "ko")
        to_lang: Summary target language (default: "ko")
    """
    # Initialize Telegram summary generator
    generator = TelegramSummaryGenerator()

    # Check PDF report directory
    reports_path = Path(reports_dir)
    if not reports_path.exists() or not reports_path.is_dir():
        logger.error(f"Report directory does not exist: {reports_dir}")
        return

    # Find report files
    report_files = list(reports_path.glob("*.md"))

    # Apply date filter
    if date_filter:
        report_files = [f for f in report_files if date_filter in f.name]

    if not report_files:
        logger.warning(f"No report files to process. Directory: {reports_dir}, Filter: {date_filter or 'None'}")
        return

    logger.info(f"Processing {len(report_files)} report files.")

    # Process each report
    for report_file in report_files:
        try:
            await generator.process_report(str(report_file), output_dir, from_lang, to_lang)
        except Exception as e:
            logger.error(f"Error processing {report_file.name}: {e}")

    logger.info("All report processing completed.")

async def main():
    """
    Main function
    """
    import argparse

    parser = argparse.ArgumentParser(description="Summarize all files in report directory to Telegram messages.")
    parser.add_argument("--reports-dir", default="reports", help="Directory path where report files are stored")
    parser.add_argument("--output-dir", default="telegram_messages", help="Directory path to save Telegram messages")
    parser.add_argument("--date", help="Process only reports from specific date (YYYYMMDD format)")
    parser.add_argument("--today", action="store_true", help="Process only today's reports")
    parser.add_argument("--report", help="Process specific report file only")
    parser.add_argument("--from-lang", default="ko", help="Report source language code (default: ko)")
    parser.add_argument("--to-lang", default="ko", help="Summary target language code (default: ko)")

    args = parser.parse_args()

    async with app.run() as parallel_app:
        logger = parallel_app.logger

        # Process specific report only
        if args.report:
            report_pdf_path = args.report
            if not os.path.exists(report_pdf_path):
                logger.error(f"Specified report file does not exist: {report_pdf_path}")
                return

            generator = TelegramSummaryGenerator()
            telegram_message = await generator.process_report(
                report_pdf_path,
                args.output_dir,
                args.from_lang,
                args.to_lang
            )

            # Print generated message
            print("\nGenerated Telegram message:")
            print("-" * 50)
            print(telegram_message)
            print("-" * 50)

        else:
            # Apply today's date filter
            date_filter = None
            if args.today:
                date_filter = datetime.now().strftime("%Y%m%d")
            elif args.date:
                date_filter = args.date

            # Process all pdf reports
            await process_all_reports(
                reports_dir=args.reports_dir,
                output_dir=args.output_dir,
                date_filter=date_filter,
                from_lang=args.from_lang,
                to_lang=args.to_lang
            )

if __name__ == "__main__":
    asyncio.run(main())
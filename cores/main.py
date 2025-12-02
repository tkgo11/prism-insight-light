import asyncio
import time
import threading
import os
import signal
from datetime import datetime

from cores.analysis import analyze_stock

if __name__ == "__main__":
    # 60분 후에 프로세스를 종료하는 타이머 함수
    def exit_after_timeout():
        time.sleep(3600)  # 60분 대기
        print("60분 타임아웃 도달: 프로세스 강제 종료")
        os.kill(os.getpid(), signal.SIGTERM)

    # 백그라운드 스레드로 타이머 시작
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    start = time.time()

    # 특정 날짜를 기준으로 분석 실행
    result = asyncio.run(analyze_stock(company_code="036570", company_name="엔씨소프트", reference_date="20251202"))

    # 결과 저장
    with open(f"엔씨소프트_분석보고서_{datetime.now().strftime('%Y%m%d')}_gpt5_1.md", "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"총 실행 시간: {end - start:.2f}초")
    print(f"최종 보고서 길이: {len(result):,} 글자")

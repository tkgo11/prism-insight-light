# 📅 PRISM-INSIGHT Crontab 설정 가이드

## 개요
PRISM-INSIGHT는 주식 시장 분석을 자동화하기 위해 crontab을 사용합니다. 이 문서는 시스템에 자동 실행 스케줄을 설정하는 방법을 설명합니다.

## 🚀 빠른 시작

### 1. 간편 설정 (권장)
```bash
# 실행 권한 부여
chmod +x setup_crontab_simple.sh

# 스크립트 실행
./setup_crontab_simple.sh
```

### 2. 고급 설정
```bash
# 실행 권한 부여
chmod +x setup_crontab.sh

# 대화형 설정
./setup_crontab.sh

# 또는 환경 변수를 사용한 자동 설정
PROJECT_DIR=/opt/prism-insight PYTHON_PATH=/usr/bin/python3 ./setup_crontab.sh --non-interactive
```

## 📋 실행 스케줄

### 기본 스케줄 (한국 시간 기준)

| 시간 | 작업 | 설명 |
|------|------|------|
| 07:00 | 데이터 업데이트 | 장 시작 전 종목 정보 갱신 |
| 09:30 | 오전 분석 | 장 시작 후 급등주 포착 및 분석 |
| 15:40 | 오후 분석 | 장 마감 후 종합 분석 |
| 03:00 | 로그 정리 | 오래된 로그 파일 삭제 |
| 18:00 | 포트폴리오 리포트 | (선택) 일일 매매 실적 보고 |

### 스케줄 설명

#### 1. **오전 분석 (09:30)**
- 장 시작 후 10분 데이터 기반
- 갭 상승, 거래량 급증 종목 포착
- 실시간 시장 동향 분석

#### 2. **오후 분석 (15:40)**
- 장 마감 후 종합 분석
- 일중 상승률, 마감 강도 분석
- 상세 AI 리포트 생성

#### 3. **데이터 업데이트 (07:00)**
- 종목 마스터 정보 갱신
- 전일 거래 데이터 수집
- 시스템 준비 상태 확인

#### 4. **로그 정리 (03:00)**
- 30일 이상 된 로그 삭제
- 디스크 공간 관리
- 시스템 최적화

## 🛠️ 수동 설정

### 1. Crontab 편집
```bash
crontab -e
```

### 2. 환경 변수 설정
```bash
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONPATH=/path/to/prism-insight
```

### 3. 스케줄 추가
```bash
# 오전 분석 (월-금)
30 9 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode morning >> logs/morning.log 2>&1

# 오후 분석 (월-금)
40 15 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode afternoon >> logs/afternoon.log 2>&1

# 데이터 업데이트 (월-금)
0 7 * * 1-5 cd /path/to/prism-insight && python update_stock_data.py >> logs/update.log 2>&1

# 로그 정리 (매일)
0 3 * * * cd /path/to/prism-insight && utils/cleanup_logs.sh
```

## 🔧 환경별 설정

### Rocky Linux / CentOS / RHEL
```bash
# SELinux 컨텍스트 설정 (필요한 경우)
sudo semanage fcontext -a -t bin_t "/path/to/prism-insight/.*\.py"
sudo restorecon -Rv /path/to/prism-insight/

# 방화벽 설정 (Telegram 봇 사용 시)
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### Ubuntu / Debian
```bash
# 시스템 Python 사용 시
sudo apt-get install python3-venv python3-pip

# 권한 설정
chmod +x *.sh
chmod +x *.py
```

### macOS
```bash
# Homebrew Python 사용 권장
brew install python3

# launchd 사용 (crontab 대신)
# ~/Library/LaunchAgents/com.prism-insight.plist 생성
```

## 🐍 Python 환경별 설정

### pyenv 사용
```bash
# .python-version 파일이 있는 경우
PYTHON_PATH="$HOME/.pyenv/shims/python"
```

### venv 사용
```bash
# 가상환경 활성화 후 실행
source /path/to/venv/bin/activate && python script.py
```

### conda 사용
```bash
# conda 환경 활성화
eval "$(conda shell.bash hook)"
conda activate prism-insight
```

## 📊 로그 확인

### 실시간 로그 모니터링
```bash
# 오전 분석 로그
tail -f logs/stock_analysis_morning_$(date +%Y%m%d).log

# 오후 분석 로그
tail -f logs/stock_analysis_afternoon_$(date +%Y%m%d).log

# 전체 로그 확인
tail -f logs/*.log
```

### 로그 분석
```bash
# 오늘의 에러 확인
grep ERROR logs/*$(date +%Y%m%d)*.log

# 성공한 분석 건수
grep "분석 완료" logs/*.log | wc -l

# 최근 5일 로그 요약
for i in {0..4}; do
    date -d "$i days ago" +%Y%m%d
    grep -c "완료" logs/*$(date -d "$i days ago" +%Y%m%d)*.log
done
```

## 🔍 문제 해결

### 1. Crontab이 실행되지 않음
```bash
# cron 서비스 확인
sudo systemctl status crond  # RHEL/CentOS
sudo systemctl status cron   # Ubuntu/Debian

# 서비스 재시작
sudo systemctl restart crond
```

### 2. Python을 찾을 수 없음
```bash
# PATH 확인
which python3

# crontab에 전체 경로 사용
/usr/bin/python3 script.py
```

### 3. 권한 오류
```bash
# 실행 권한 부여
chmod +x *.py *.sh

# 소유권 확인
ls -la

# 필요시 소유권 변경
chown -R $USER:$USER /path/to/prism-insight
```

### 4. 시간대 문제
```bash
# 시스템 시간대 확인
timedatectl

# 한국 시간대 설정
sudo timedatectl set-timezone Asia/Seoul

# crontab에서 시간대 지정
TZ=Asia/Seoul
30 9 * * 1-5 command
```

## 📝 유지보수

### 백업
```bash
# crontab 백업
crontab -l > crontab_backup_$(date +%Y%m%d).txt

# 복원
crontab crontab_backup_20250113.txt
```

### 임시 중지
```bash
# 전체 중지
crontab -r

# 특정 작업만 주석 처리
crontab -e
# 30 9 * * 1-5 ...  <- # 추가
```

### 테스트
```bash
# 수동 실행 테스트
cd /path/to/prism-insight
python stock_analysis_orchestrator.py --mode morning

# cron 환경 시뮬레이션
env -i SHELL=/bin/bash PATH=/usr/bin:/bin python script.py

# 다음 실행 시간 확인
crontab -l | grep -v "^#" | cut -f 1-5 -d ' ' | while read schedule; do
    echo "$schedule -> $(date -d "$schedule" 2>/dev/null || echo "매일/매주 반복")"
done
```

## 🎯 모범 사례

### 1. **로그 로테이션 설정**
```bash
# /etc/logrotate.d/prism-insight 생성
/path/to/prism-insight/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 user group
    sharedscripts
}
```

### 2. **에러 알림 설정**
```bash
# 에러 발생 시 이메일 알림
MAILTO=your-email@example.com
30 9 * * 1-5 /path/to/script.py || echo "오전 분석 실패" | mail -s "PRISM-INSIGHT 에러" $MAILTO
```

### 3. **건강 체크**
```bash
# 실행 상태 모니터링 스크립트
#!/bin/bash
# health_check.sh

LAST_RUN=$(find logs -name "*$(date +%Y%m%d)*.log" -mmin -60 | wc -l)
if [ $LAST_RUN -eq 0 ]; then
    echo "경고: 최근 1시간 내 실행 기록 없음"
    # 알림 전송 로직
fi
```

### 4. **리소스 제한**
```bash
# CPU/메모리 사용량 제한
30 9 * * 1-5 nice -n 10 ionice -c 3 timeout 3600 python script.py

# nice: CPU 우선순위 낮춤
# ionice: I/O 우선순위 낮춤
# timeout: 최대 실행 시간 제한 (1시간)
```

## 📚 참고 자료

### Cron 표현식 가이드

| 필드 | 값 | 설명 |
|------|-----|------|
| 분 | 0-59 | 매시 정각: 0 |
| 시 | 0-23 | 오전 9시: 9 |
| 일 | 1-31 | 매일: * |
| 월 | 1-12 | 매월: * |
| 요일 | 0-7 | 월-금: 1-5 (0,7=일요일) |

### 특수 문자

- `*` : 모든 값
- `,` : 값 목록 (예: 1,3,5)
- `-` : 범위 (예: 1-5)
- `/` : 간격 (예: */5 = 5분마다)

### 유용한 예시

```bash
# 매 30분마다
*/30 * * * * command

# 평일 오전 9시-오후 6시 매시간
0 9-18 * * 1-5 command

# 매주 월요일 오전 8시
0 8 * * 1 command

# 매월 1일과 15일
0 0 1,15 * * command

# 분기별 (1,4,7,10월 1일)
0 0 1 1,4,7,10 * command
```

## ⚠️ 주의사항

1. **시장 휴일 처리**
   - 스크립트 내부에서 휴일 체크 로직 구현
   - KRX 휴장일 캘린더 참조

2. **시간대 설정**
   - 서버 시간대가 KST가 아닌 경우 시간 조정 필요
   - UTC 서버의 경우: 9시간 차이 고려

3. **권한 관리**
   - 민감한 정보(API 키 등)는 환경 변수 사용
   - 로그 파일에 개인정보 노출 주의

4. **백업 정책**
   - 정기적인 데이터베이스 백업
   - 중요 로그 아카이빙

## 🤝 도움 받기

문제가 발생하거나 도움이 필요한 경우:

1. [GitHub Issues](https://github.com/yourusername/prism-insight/issues)에 문의
2. [텔레그램 채널](https://t.me/stock_ai_agent)에서 커뮤니티 지원
3. 로그 파일 확인 (`logs/` 디렉토리)
4. 이 문서의 문제 해결 섹션 참조

---

*최종 업데이트: 2025년 1월*

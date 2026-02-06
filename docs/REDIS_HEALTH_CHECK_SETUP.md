# Redis Health Check 설정 가이드

Upstash Redis 무료 티어의 비활성화를 방지하기 위한 Health Check 설정 방법입니다.

## 📋 개요

Upstash Redis 무료 티어는 일정 기간 동안 트래픽이 없으면 자동으로 아카이빙됩니다.
`redis_health_check.py` 스크립트를 주기적으로 실행하여 데이터베이스를 활성 상태로 유지합니다.

## 🔧 Health Check가 수행하는 작업

1. **PING** - Redis 연결 확인
2. **타임스탬프 저장** - 마지막 체크 시간 저장 (24시간 TTL)
3. **카운터 증가** - 총 체크 횟수 카운트 (30일 TTL)
4. **로그 추가** - 체크 이력 기록 (최근 100개, 7일 TTL)
5. **데이터 검증** - 저장된 데이터 읽기 확인

## 🚀 사용 방법

### 1. 직접 실행

```bash
# 프로젝트 루트에서
python messaging/redis_health_check.py
```

### 2. Python 코드에서 호출

```python
# 동기 방식
from messaging.redis_health_check import run_health_check

results = run_health_check()
print(f"Success: {results['success']}")

# 비동기 방식
import asyncio
from messaging.redis_health_check import run_health_check_async

results = await run_health_check_async()
```

## ⏰ 크론탭 설정 (Linux/Mac)

### 매일 오전 9시 실행

```bash
# 크론탭 편집
crontab -e

# 아래 라인 추가 (경로는 실제 프로젝트 경로로 수정)
0 9 * * * cd /path/to/prism-insight && /usr/bin/python3 messaging/redis_health_check.py >> /tmp/redis_health_check.log 2>&1
```

### 매일 오전 9시, 오후 9시 실행 (하루 2회)

```bash
0 9,21 * * * cd /path/to/prism-insight && /usr/bin/python3 messaging/redis_health_check.py >> /tmp/redis_health_check.log 2>&1
```

### 매주 월/수/금 오전 9시 실행

```bash
0 9 * * 1,3,5 cd /path/to/prism-insight && /usr/bin/python3 messaging/redis_health_check.py >> /tmp/redis_health_check.log 2>&1
```

### Python 경로 확인

```bash
which python3
# 또는
which python
```

## 🪟 Windows 작업 스케줄러 설정

### 1. 배치 파일 생성

`redis_health_check.bat` 파일을 프로젝트 루트에 생성:

```batch
@echo off
cd /d C:\path\to\prism-insight
python messaging\redis_health_check.py >> redis_health_check.log 2>&1
```

### 2. 작업 스케줄러 등록

1. `작업 스케줄러` 실행
2. `기본 작업 만들기` 클릭
3. 이름: `Redis Health Check`
4. 트리거: `매일` 선택, 시간 설정
5. 동작: `프로그램 시작` 선택
6. 프로그램: `C:\path\to\prism-insight\redis_health_check.bat`
7. 완료

## 🐳 Docker 환경에서 실행

### docker-compose.yml에 추가

```yaml
services:
  health-checker:
    build: .
    command: python messaging/redis_health_check.py
    env_file:
      - .env
    restart: on-failure
    # 크론 작업으로 실행하려면
    # command: sh -c "while true; do python messaging/redis_health_check.py; sleep 86400; done"
```

### Kubernetes CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: redis-health-check
spec:
  schedule: "0 9 * * *"  # 매일 오전 9시
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: health-checker
            image: prism-insight:latest
            command: ["python", "messaging/redis_health_check.py"]
            envFrom:
            - secretRef:
                name: redis-credentials
          restartPolicy: OnFailure
```

## 📊 로그 확인

### 크론탭 로그 확인

```bash
# 로그 파일 확인
tail -f /tmp/redis_health_check.log

# 최근 실행 결과
tail -20 /tmp/redis_health_check.log
```

### 성공 예시

```
2025-01-15 09:00:01 - redis_health_check - INFO - Starting Redis health check...
2025-01-15 09:00:01 - redis_health_check - INFO - ✓ Redis connected: https://skilled-**********.upstash.io...
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ PING: PONG
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ SET timestamp: 2025-01-15T09:00:02.123456
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ INCR counter: 42
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ LPUSH log entry
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ GET timestamp: 2025-01-15T09:00:02.123456
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ LLEN log count: 42
2025-01-15 09:00:02 - redis_health_check - INFO - ============================================================
2025-01-15 09:00:02 - redis_health_check - INFO - ✓ Health check completed successfully
2025-01-15 09:00:02 - redis_health_check - INFO -   - Total checks performed: 42
2025-01-15 09:00:02 - redis_health_check - INFO -   - Log entries: 42
2025-01-15 09:00:02 - redis_health_check - INFO - ============================================================
2025-01-15 09:00:02 - redis_health_check - INFO - Health check completed successfully!
```

## 🔍 문제 해결

### 환경 변수 오류

```
ValueError: Redis credentials not found
```

**해결:** `.env` 파일에 다음 변수가 설정되어 있는지 확인:
```
UPSTASH_REDIS_REST_URL=https://...
UPSTASH_REDIS_REST_TOKEN=...
```

### 패키지 없음 오류

```
ImportError: upstash-redis package not installed
```

**해결:**
```bash
pip install upstash-redis
```

### 크론탭이 실행되지 않음

1. 크론 서비스 확인:
   ```bash
   sudo service cron status
   ```

2. 크론 로그 확인:
   ```bash
   grep CRON /var/log/syslog
   ```

3. 절대 경로 사용 확인
4. Python 경로 확인 (`which python3`)

## 📈 권장 실행 주기

- **매일 1회**: 가장 안전하고 권장됨
- **매주 2-3회**: 최소 권장 (Upstash가 "몇 주" 단위로 체크하므로)
- **매일 2회**: 더 안전하게 유지하고 싶은 경우

## 💡 추가 팁

1. **로그 로테이션**: 로그 파일이 너무 커지지 않도록 logrotate 설정
2. **모니터링**: Health check 실패 시 알림 설정 (메일, 슬랙 등)
3. **백업**: 중요한 데이터는 별도로 백업 유지
4. **유료 플랜 고려**: 트래픽이 많거나 안정성이 중요한 경우

## 📞 문제 발생 시

1. 로그 확인
2. Redis 대시보드에서 상태 확인
3. `.env` 파일의 접속 정보 확인
4. 수동 실행으로 테스트: `python messaging/redis_health_check.py`

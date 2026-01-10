#!/usr/bin/env python3
"""
분석 종목 성과 심층 분석 리포트

트리거 유형별, 매매/관망별 성과를 통계적으로 분석하여
향후 필터 조정 여부를 결정하는 데 도움을 주는 리포트를 생성합니다.

Usage:
    python performance_analysis_report.py                    # 전체 리포트
    python performance_analysis_report.py --format markdown  # 마크다운 출력
    python performance_analysis_report.py --output report.md # 파일로 저장
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from collections import defaultdict

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# 통계 라이브러리 (선택적)
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy 패키지가 없어 통계적 유의성 검정이 비활성화됩니다.")


class PerformanceAnalyzer:
    """분석 성과 심층 분석기"""

    def __init__(self, db_path: str = None):
        """
        Args:
            db_path: SQLite DB 경로
        """
        self.db_path = db_path or str(DB_PATH)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def connect_db(self) -> sqlite3.Connection:
        """DB 연결"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all_completed_data(self) -> List[Dict[str, Any]]:
        """완료된 모든 추적 데이터 조회"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT *
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY analyzed_date DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_overview_stats(self) -> Dict[str, Any]:
        """전체 현황 통계"""
        conn = self.connect_db()
        try:
            # 전체 추적 상태별 건수
            cursor = conn.execute("""
                SELECT
                    tracking_status,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_counts = {row['tracking_status']: row['count'] for row in cursor.fetchall()}

            # 전체 매매/관망 건수
            cursor = conn.execute("""
                SELECT
                    was_traded,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY was_traded
            """)
            traded_counts = {}
            for row in cursor.fetchall():
                key = 'traded' if row['was_traded'] else 'watched'
                traded_counts[key] = row['count']

            # 기간별 총 건수
            cursor = conn.execute("""
                SELECT
                    strftime('%Y-%m', analyzed_date) as month,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY month
                ORDER BY month DESC
                LIMIT 6
            """)
            monthly_counts = [(row['month'], row['count']) for row in cursor.fetchall()]

            return {
                'status_counts': status_counts,
                'traded_counts': traded_counts,
                'monthly_counts': monthly_counts,
                'total': sum(status_counts.values())
            }
        finally:
            conn.close()

    def analyze_by_trigger_type(self) -> Dict[str, Dict[str, Any]]:
        """트리거 유형별 성과 분석"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    was_traded,
                    tracked_7d_return,
                    tracked_14d_return,
                    tracked_30d_return,
                    buy_score,
                    risk_reward_ratio,
                    target_price,
                    stop_loss,
                    analyzed_price
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
            """)

            # 트리거 유형별 데이터 수집
            trigger_data = defaultdict(lambda: {
                'returns_7d': [],
                'returns_14d': [],
                'returns_30d': [],
                'traded_returns_30d': [],
                'watched_returns_30d': [],
                'buy_scores': [],
                'rr_ratios': [],
                'count': 0,
                'traded_count': 0
            })

            for row in cursor.fetchall():
                trigger_type = row['trigger_type'] or 'unknown'
                data = trigger_data[trigger_type]

                data['count'] += 1
                if row['was_traded']:
                    data['traded_count'] += 1

                if row['tracked_7d_return'] is not None:
                    data['returns_7d'].append(row['tracked_7d_return'])
                if row['tracked_14d_return'] is not None:
                    data['returns_14d'].append(row['tracked_14d_return'])
                if row['tracked_30d_return'] is not None:
                    data['returns_30d'].append(row['tracked_30d_return'])
                    if row['was_traded']:
                        data['traded_returns_30d'].append(row['tracked_30d_return'])
                    else:
                        data['watched_returns_30d'].append(row['tracked_30d_return'])

                if row['buy_score'] is not None:
                    data['buy_scores'].append(row['buy_score'])
                if row['risk_reward_ratio'] is not None:
                    data['rr_ratios'].append(row['risk_reward_ratio'])

            # 통계 계산
            results = {}
            for trigger_type, data in trigger_data.items():
                results[trigger_type] = {
                    'count': data['count'],
                    'traded_count': data['traded_count'],
                    'traded_rate': data['traded_count'] / data['count'] if data['count'] > 0 else 0,
                    'avg_7d_return': self._safe_mean(data['returns_7d']),
                    'avg_14d_return': self._safe_mean(data['returns_14d']),
                    'avg_30d_return': self._safe_mean(data['returns_30d']),
                    'std_30d_return': self._safe_std(data['returns_30d']),
                    'win_rate_30d': self._win_rate(data['returns_30d']),
                    'traded_avg_30d': self._safe_mean(data['traded_returns_30d']),
                    'watched_avg_30d': self._safe_mean(data['watched_returns_30d']),
                    'avg_buy_score': self._safe_mean(data['buy_scores']),
                    'avg_rr_ratio': self._safe_mean(data['rr_ratios']),
                    # 원본 데이터 (통계 검정용)
                    '_returns_30d': data['returns_30d'],
                    '_traded_returns_30d': data['traded_returns_30d'],
                    '_watched_returns_30d': data['watched_returns_30d']
                }

            return results
        finally:
            conn.close()

    def analyze_traded_vs_watched(self) -> Dict[str, Any]:
        """매매 vs 관망 성과 비교"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    was_traded,
                    tracked_7d_return,
                    tracked_14d_return,
                    tracked_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
            """)

            traded_returns = {'7d': [], '14d': [], '30d': []}
            watched_returns = {'7d': [], '14d': [], '30d': []}

            for row in cursor.fetchall():
                target = traded_returns if row['was_traded'] else watched_returns

                if row['tracked_7d_return'] is not None:
                    target['7d'].append(row['tracked_7d_return'])
                if row['tracked_14d_return'] is not None:
                    target['14d'].append(row['tracked_14d_return'])
                if row['tracked_30d_return'] is not None:
                    target['30d'].append(row['tracked_30d_return'])

            result = {
                'traded': {
                    'count': len(traded_returns['30d']),
                    'avg_7d': self._safe_mean(traded_returns['7d']),
                    'avg_14d': self._safe_mean(traded_returns['14d']),
                    'avg_30d': self._safe_mean(traded_returns['30d']),
                    'std_30d': self._safe_std(traded_returns['30d']),
                    'win_rate': self._win_rate(traded_returns['30d']),
                    '_returns_30d': traded_returns['30d']
                },
                'watched': {
                    'count': len(watched_returns['30d']),
                    'avg_7d': self._safe_mean(watched_returns['7d']),
                    'avg_14d': self._safe_mean(watched_returns['14d']),
                    'avg_30d': self._safe_mean(watched_returns['30d']),
                    'std_30d': self._safe_std(watched_returns['30d']),
                    'win_rate': self._win_rate(watched_returns['30d']),
                    '_returns_30d': watched_returns['30d']
                }
            }

            # 통계적 유의성 검정
            if SCIPY_AVAILABLE and traded_returns['30d'] and watched_returns['30d']:
                if len(traded_returns['30d']) >= 5 and len(watched_returns['30d']) >= 5:
                    t_stat, p_value = stats.ttest_ind(
                        traded_returns['30d'],
                        watched_returns['30d'],
                        equal_var=False  # Welch's t-test
                    )
                    result['t_test'] = {
                        't_statistic': t_stat,
                        'p_value': p_value,
                        'significant': p_value < 0.05
                    }

            return result
        finally:
            conn.close()

    def analyze_rr_threshold_impact(self) -> Dict[str, Any]:
        """손익비 기준별 성과 분석

        손익비 1.5, 1.75, 2.0, 2.5 기준별로
        매매/관망 시 성과가 어떻게 달랐는지 분석
        """
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    risk_reward_ratio,
                    was_traded,
                    tracked_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND risk_reward_ratio IS NOT NULL
            """)

            # 손익비 구간별 데이터 수집
            thresholds = [0, 1.0, 1.5, 1.75, 2.0, 2.5, 100]
            rr_data = {}

            for i in range(len(thresholds) - 1):
                low, high = thresholds[i], thresholds[i+1]
                label = f"{low:.1f}~{high:.1f}" if high < 100 else f"{low:.1f}+"
                rr_data[label] = {
                    'traded_returns': [],
                    'watched_returns': [],
                    'all_returns': []
                }

            for row in cursor.fetchall():
                rr = row['risk_reward_ratio']
                ret = row['tracked_30d_return']

                if rr is None or ret is None:
                    continue

                # 해당 구간 찾기
                for i in range(len(thresholds) - 1):
                    low, high = thresholds[i], thresholds[i+1]
                    if low <= rr < high:
                        label = f"{low:.1f}~{high:.1f}" if high < 100 else f"{low:.1f}+"
                        rr_data[label]['all_returns'].append(ret)
                        if row['was_traded']:
                            rr_data[label]['traded_returns'].append(ret)
                        else:
                            rr_data[label]['watched_returns'].append(ret)
                        break

            # 통계 계산
            result = {}
            for label, data in rr_data.items():
                result[label] = {
                    'total_count': len(data['all_returns']),
                    'traded_count': len(data['traded_returns']),
                    'watched_count': len(data['watched_returns']),
                    'avg_all_return': self._safe_mean(data['all_returns']),
                    'avg_traded_return': self._safe_mean(data['traded_returns']),
                    'avg_watched_return': self._safe_mean(data['watched_returns']),
                    'win_rate_all': self._win_rate(data['all_returns']),
                    'win_rate_watched': self._win_rate(data['watched_returns'])
                }

            return result
        finally:
            conn.close()

    def get_missed_opportunities(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """놓친 기회 분석 (관망했는데 크게 상승한 종목)"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    buy_score,
                    min_score,
                    risk_reward_ratio,
                    skip_reason
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND was_traded = 0
                  AND tracked_30d_return > 0.1
                ORDER BY tracked_30d_return DESC
                LIMIT ?
            """, (top_n,))

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_avoided_losses(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """회피한 손실 분석 (관망했는데 크게 하락한 종목)"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    buy_score,
                    min_score,
                    risk_reward_ratio,
                    skip_reason
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND was_traded = 0
                  AND tracked_30d_return < -0.1
                ORDER BY tracked_30d_return ASC
                LIMIT ?
            """, (top_n,))

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def generate_recommendations(self) -> List[str]:
        """데이터 기반 권고사항 생성"""
        recommendations = []

        # 매매 vs 관망 분석
        tv = self.analyze_traded_vs_watched()
        if tv['traded']['count'] >= 10 and tv['watched']['count'] >= 10:
            traded_avg = tv['traded']['avg_30d'] or 0
            watched_avg = tv['watched']['avg_30d'] or 0

            if watched_avg > traded_avg and watched_avg - traded_avg > 0.05:
                recommendations.append(
                    f"⚠️ 관망 종목({watched_avg*100:.1f}%)이 매매 종목({traded_avg*100:.1f}%)보다 "
                    f"30일 평균 수익률이 높습니다. 필터 완화를 고려하세요."
                )
            elif traded_avg > watched_avg and traded_avg - watched_avg > 0.05:
                recommendations.append(
                    f"✅ 매매 종목({traded_avg*100:.1f}%)이 관망 종목({watched_avg*100:.1f}%)보다 "
                    f"30일 평균 수익률이 높습니다. 현재 필터가 효과적입니다."
                )

            # 통계적 유의성
            if 't_test' in tv and tv['t_test']['significant']:
                recommendations.append(
                    f"📊 매매/관망 수익률 차이가 통계적으로 유의합니다 (p={tv['t_test']['p_value']:.4f})"
                )

        # 트리거 유형별 분석
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            # 가장 성과 좋은 트리거
            best_trigger = max(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999
            )
            if best_trigger[1]['avg_30d_return'] and best_trigger[1]['count'] >= 5:
                recommendations.append(
                    f"🏆 가장 좋은 트리거: '{best_trigger[0]}' "
                    f"(30일 평균 {best_trigger[1]['avg_30d_return']*100:.1f}%, "
                    f"승률 {best_trigger[1]['win_rate_30d']*100:.0f}%)"
                )

            # 관망했는데 성과 좋았던 트리거
            for trigger, data in trigger_stats.items():
                watched_avg = data.get('watched_avg_30d')
                if watched_avg and watched_avg > 0.1 and len(data['_watched_returns_30d']) >= 3:
                    recommendations.append(
                        f"💡 '{trigger}' 트리거의 관망 종목이 30일 평균 "
                        f"{watched_avg*100:.1f}% 상승했습니다. 해당 유형 필터 완화 검토 필요."
                    )

        # 손익비 구간별 분석
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            # 1.5~2.0 구간의 관망 종목 성과
            for label in ['1.5~1.75', '1.75~2.0']:
                if label in rr_stats:
                    data = rr_stats[label]
                    if data['watched_count'] >= 5:
                        avg_ret = data['avg_watched_return']
                        win_rate = data['win_rate_watched']
                        if avg_ret and avg_ret > 0.05 and win_rate and win_rate > 0.5:
                            recommendations.append(
                                f"📈 손익비 {label} 구간 관망 종목: "
                                f"평균 {avg_ret*100:.1f}%, 승률 {win_rate*100:.0f}%. "
                                f"손익비 기준 완화 시 추가 수익 가능성 있음."
                            )

        # 데이터 부족 경고
        total_completed = sum(
            1 for d in [tv['traded'], tv['watched']]
            for _ in range(d['count'])
        )
        if total_completed < 30:
            recommendations.append(
                f"⏳ 완료된 추적 데이터가 {total_completed}건으로 부족합니다. "
                f"최소 30건 이상 누적 후 의사결정을 권장합니다."
            )

        return recommendations

    def generate_full_report(self, format: str = "text") -> str:
        """전체 분석 리포트 생성

        Args:
            format: "text" 또는 "markdown"

        Returns:
            리포트 문자열
        """
        if format == "markdown":
            return self._generate_markdown_report()
        else:
            return self._generate_text_report()

    def _generate_text_report(self) -> str:
        """텍스트 형식 리포트"""
        lines = []
        sep = "="*70

        lines.append(sep)
        lines.append(f"📊 PRISM-INSIGHT 트리거 성과 분석 리포트")
        lines.append(f"생성일: {self.today}")
        lines.append(sep)
        lines.append("")

        # 1. 전체 현황
        overview = self.get_overview_stats()
        lines.append("## 1. 전체 현황")
        lines.append("-"*40)
        lines.append(f"  총 추적 건수: {overview['total']}")
        for status, count in overview['status_counts'].items():
            status_kr = {'pending': '대기중', 'in_progress': '추적중', 'completed': '완료'}.get(status, status)
            lines.append(f"    - {status_kr}: {count}건")
        lines.append(f"  매매: {overview['traded_counts'].get('traded', 0)}건")
        lines.append(f"  관망: {overview['traded_counts'].get('watched', 0)}건")
        lines.append("")

        # 2. 매매 vs 관망
        lines.append("## 2. 매매 vs 관망 성과 비교")
        lines.append("-"*40)
        tv = self.analyze_traded_vs_watched()
        for label, data in [('매매', tv['traded']), ('관망', tv['watched'])]:
            if data['count'] > 0:
                lines.append(f"  [{label}] (n={data['count']})")
                lines.append(f"    7일 평균: {self._fmt_pct(data['avg_7d'])}")
                lines.append(f"    14일 평균: {self._fmt_pct(data['avg_14d'])}")
                lines.append(f"    30일 평균: {self._fmt_pct(data['avg_30d'])} (표준편차: {self._fmt_pct(data['std_30d'])})")
                lines.append(f"    승률: {self._fmt_pct(data['win_rate'])}")
        if 't_test' in tv:
            sig = "유의" if tv['t_test']['significant'] else "비유의"
            lines.append(f"  [t-검정] p-value={tv['t_test']['p_value']:.4f} ({sig})")
        lines.append("")

        # 3. 트리거 유형별
        lines.append("## 3. 트리거 유형별 성과")
        lines.append("-"*40)
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            # 헤더
            lines.append(f"{'트리거':<25} {'건수':>6} {'매매율':>8} {'30일평균':>10} {'승률':>8}")
            lines.append("-"*60)
            # 30일 평균 수익률 기준 정렬
            sorted_triggers = sorted(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999,
                reverse=True
            )
            for trigger, data in sorted_triggers:
                lines.append(
                    f"{trigger:<25} {data['count']:>6} "
                    f"{self._fmt_pct(data['traded_rate']):>8} "
                    f"{self._fmt_pct(data['avg_30d_return']):>10} "
                    f"{self._fmt_pct(data['win_rate_30d']):>8}"
                )
        else:
            lines.append("  데이터 없음")
        lines.append("")

        # 4. 손익비 구간별
        lines.append("## 4. 손익비 구간별 성과")
        lines.append("-"*40)
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            lines.append(f"{'구간':<12} {'건수':>6} {'매매':>6} {'관망':>6} {'전체평균':>10} {'관망평균':>10}")
            lines.append("-"*55)
            for label, data in rr_stats.items():
                if data['total_count'] > 0:
                    lines.append(
                        f"{label:<12} {data['total_count']:>6} "
                        f"{data['traded_count']:>6} {data['watched_count']:>6} "
                        f"{self._fmt_pct(data['avg_all_return']):>10} "
                        f"{self._fmt_pct(data['avg_watched_return']):>10}"
                    )
        else:
            lines.append("  데이터 없음")
        lines.append("")

        # 5. 놓친 기회
        lines.append("## 5. 놓친 기회 (관망 → 10%+ 상승)")
        lines.append("-"*40)
        missed = self.get_missed_opportunities(5)
        if missed:
            for item in missed:
                lines.append(
                    f"  [{item['ticker']}] {item['company_name']} "
                    f"({item['trigger_type'] or 'unknown'})"
                )
                lines.append(
                    f"    {item['analyzed_price']:,.0f} → {item['tracked_30d_price']:,.0f} "
                    f"({self._fmt_pct(item['tracked_30d_return'])})"
                )
                lines.append(f"    점수: {item['buy_score']}/{item['min_score']}, 손익비: {item['risk_reward_ratio']:.2f}")
                lines.append(f"    사유: {item['skip_reason']}")
        else:
            lines.append("  해당 없음")
        lines.append("")

        # 6. 회피한 손실
        lines.append("## 6. 회피한 손실 (관망 → 10%+ 하락)")
        lines.append("-"*40)
        avoided = self.get_avoided_losses(5)
        if avoided:
            for item in avoided:
                lines.append(
                    f"  [{item['ticker']}] {item['company_name']} "
                    f"({item['trigger_type'] or 'unknown'})"
                )
                lines.append(
                    f"    {item['analyzed_price']:,.0f} → {item['tracked_30d_price']:,.0f} "
                    f"({self._fmt_pct(item['tracked_30d_return'])})"
                )
                lines.append(f"    점수: {item['buy_score']}/{item['min_score']}, 손익비: {item['risk_reward_ratio']:.2f}")
        else:
            lines.append("  해당 없음")
        lines.append("")

        # 7. 권고사항
        lines.append("## 7. 데이터 기반 권고사항")
        lines.append("-"*40)
        recommendations = self.generate_recommendations()
        if recommendations:
            for rec in recommendations:
                lines.append(f"  • {rec}")
        else:
            lines.append("  권고사항 없음 (데이터 추가 수집 필요)")
        lines.append("")

        lines.append(sep)
        lines.append("© PRISM-INSIGHT 투자 전략 분석 시스템")
        lines.append(sep)

        return "\n".join(lines)

    def _generate_markdown_report(self) -> str:
        """마크다운 형식 리포트"""
        lines = []

        lines.append(f"# 📊 PRISM-INSIGHT 트리거 성과 분석 리포트")
        lines.append(f"")
        lines.append(f"**생성일**: {self.today}")
        lines.append("")

        # 1. 전체 현황
        overview = self.get_overview_stats()
        lines.append("## 1. 전체 현황")
        lines.append("")
        lines.append(f"- **총 추적 건수**: {overview['total']}")
        for status, count in overview['status_counts'].items():
            status_kr = {'pending': '대기중', 'in_progress': '추적중', 'completed': '완료'}.get(status, status)
            lines.append(f"  - {status_kr}: {count}건")
        lines.append(f"- **매매**: {overview['traded_counts'].get('traded', 0)}건")
        lines.append(f"- **관망**: {overview['traded_counts'].get('watched', 0)}건")
        lines.append("")

        # 2. 매매 vs 관망
        lines.append("## 2. 매매 vs 관망 성과 비교")
        lines.append("")
        tv = self.analyze_traded_vs_watched()
        lines.append("| 구분 | 건수 | 7일 | 14일 | 30일 | 승률 |")
        lines.append("|------|------|-----|------|------|------|")
        for label, data in [('매매', tv['traded']), ('관망', tv['watched'])]:
            lines.append(
                f"| {label} | {data['count']} | "
                f"{self._fmt_pct(data['avg_7d'])} | "
                f"{self._fmt_pct(data['avg_14d'])} | "
                f"{self._fmt_pct(data['avg_30d'])} | "
                f"{self._fmt_pct(data['win_rate'])} |"
            )
        if 't_test' in tv:
            sig = "✅ 유의" if tv['t_test']['significant'] else "❌ 비유의"
            lines.append("")
            lines.append(f"> **t-검정**: p-value = {tv['t_test']['p_value']:.4f} ({sig})")
        lines.append("")

        # 3. 트리거 유형별
        lines.append("## 3. 트리거 유형별 성과")
        lines.append("")
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            lines.append("| 트리거 유형 | 건수 | 매매율 | 30일 평균 | 승률 |")
            lines.append("|-------------|------|--------|-----------|------|")
            sorted_triggers = sorted(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999,
                reverse=True
            )
            for trigger, data in sorted_triggers:
                lines.append(
                    f"| {trigger} | {data['count']} | "
                    f"{self._fmt_pct(data['traded_rate'])} | "
                    f"{self._fmt_pct(data['avg_30d_return'])} | "
                    f"{self._fmt_pct(data['win_rate_30d'])} |"
                )
        else:
            lines.append("*데이터 없음*")
        lines.append("")

        # 4. 손익비 구간별
        lines.append("## 4. 손익비 구간별 성과")
        lines.append("")
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            lines.append("| 손익비 구간 | 전체 | 매매 | 관망 | 전체 평균 | 관망 평균 |")
            lines.append("|-------------|------|------|------|-----------|-----------|")
            for label, data in rr_stats.items():
                if data['total_count'] > 0:
                    lines.append(
                        f"| {label} | {data['total_count']} | "
                        f"{data['traded_count']} | {data['watched_count']} | "
                        f"{self._fmt_pct(data['avg_all_return'])} | "
                        f"{self._fmt_pct(data['avg_watched_return'])} |"
                    )
        else:
            lines.append("*데이터 없음*")
        lines.append("")

        # 5. 권고사항
        lines.append("## 5. 데이터 기반 권고사항")
        lines.append("")
        recommendations = self.generate_recommendations()
        if recommendations:
            for rec in recommendations:
                lines.append(f"- {rec}")
        else:
            lines.append("*권고사항 없음 (데이터 추가 수집 필요)*")
        lines.append("")

        lines.append("---")
        lines.append("*© PRISM-INSIGHT 투자 전략 분석 시스템*")

        return "\n".join(lines)

    # === 유틸리티 메서드 ===

    def _safe_mean(self, values: List[float]) -> Optional[float]:
        """안전한 평균 계산"""
        if not values:
            return None
        return sum(values) / len(values)

    def _safe_std(self, values: List[float]) -> Optional[float]:
        """안전한 표준편차 계산"""
        if not values or len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return variance ** 0.5

    def _win_rate(self, returns: List[float]) -> Optional[float]:
        """승률 계산 (수익률 > 0인 비율)"""
        if not returns:
            return None
        winners = sum(1 for r in returns if r > 0)
        return winners / len(returns)

    def _fmt_pct(self, value: Optional[float]) -> str:
        """퍼센트 포맷팅"""
        if value is None:
            return "N/A"
        return f"{value*100:+.1f}%"


def main():
    parser = argparse.ArgumentParser(
        description="분석 종목 성과 심층 분석 리포트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
    python performance_analysis_report.py                    # 텍스트 리포트
    python performance_analysis_report.py --format markdown  # 마크다운 리포트
    python performance_analysis_report.py --output report.md # 파일로 저장
        """
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="출력 형식 (기본: text)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="리포트를 저장할 파일 경로"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite DB 경로 (기본: ./stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    analyzer = PerformanceAnalyzer(db_path=args.db)
    report = analyzer.generate_full_report(format=args.format)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"리포트가 저장되었습니다: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()

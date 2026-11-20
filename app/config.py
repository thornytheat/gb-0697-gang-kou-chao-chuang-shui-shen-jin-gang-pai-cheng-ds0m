import os
from decimal import Decimal
from pathlib import Path
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/app-data"))
RULE_VERSION = "2026.11.20.1"
MAX_BYTES = 2_000_000

# 分段进港
TIDE_MAX_GAP_S = Decimal(10800)      # 相邻潮位最长插值间隔（秒）
UNDER_KEEL_M = Decimal("1")          # 富余安全余量
SQUAT_COEF = Decimal("0.008")        # 船体下沉 = 系数 * 航速(节)^2
MIN_SPEED_KN = Decimal("3")
SPEED_STEP_KN = Decimal("0.5")
PLAN_STEP_S = Decimal(60)            # 搜索起航时刻的最小步长
PLAN_COARSE_S = Decimal(600)

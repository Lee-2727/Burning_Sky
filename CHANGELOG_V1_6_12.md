# Burning Sky V1.6.12

## 修复
- 修复 `engine.py` 使用 `from burning_sky.algorithm import *` 时，`enforce_single_mode_special_verdicts` 未加入 `__all__` 导致的 NameError。
- 本次仅修复函数导出遗漏，不改动五条特殊判定规则及预测算法。

## 验证
- 已验证引擎实际导入路径可取得该函数。

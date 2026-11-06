FIX3 云高估算染色通道修复（实验版）
1. 修复 FIX2 对 strict_fraction > 0 的循环依赖。
2. 当主光路 unresolved、有采样晴空间隔、采样覆盖满足要求、云高回退确实发生且连续云面可用时，输出严格/可能两种遮挡假设的几何情景中点估算。
3. 输出 estimated_light_envelope，包括上下界与采样分钟数。
4. 保持 verified 与 estimated_scenario 区分；0%估算不等于物理上证实无光路。
5. 本版不是完整的多云高情景重追踪，仍依赖现有连续云面计算；不能保证所有案例都有非零估算。
6. 已进行 Python 编译检查，未完成 Windows GUI 端到端及沈阳缓存离线重算。

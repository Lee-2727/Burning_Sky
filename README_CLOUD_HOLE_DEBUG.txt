Burning Sky 云洞查找 Debug 版

基于 V1.7.10 FIX5 云洞显示统一版。
运行桌面版并执行预测后，会自动记录云洞查找过程（输入采样点、理论极限云距、阈值、最终阶段/角度交集/云洞候选）。
日志位置：程序当前工作目录\debug_logs\cloud_hole_search_debug.log（JSON Lines，每次扫描一行）。
如果当前目录不可写，会回退到用户目录\BurningSky_debug_logs\cloud_hole_search_debug.log。
日志仅用于本地调试，不显示到网页端。

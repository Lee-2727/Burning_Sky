Burning Sky Tkinter 云高三级回退实验版（基于 V2.0.4，不是 V2.0.5 最新算法）
一级：气压层云量；二级：相对湿度>=85%的气压层；三级：按低中高云典型高度选取层。
云高来源统计写入 analysis_state 的 direction_results 内 inferred.cloud_height_reconstruction（具体层级可能嵌套）。
注意：目前是中心情景试验，尚未完成偏低/偏高多情景重算；所有估算参与的光路只能视为情景估计。
不新增气象 API 请求。不能保证所有案例都能算出有效染色占比。

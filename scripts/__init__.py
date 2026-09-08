# scripts/__init__.py
# scripts 包：开发 CLI 壳层（被 [project.scripts] gaokao 入口引用故需可导入；
# 照 AlgoNotes 把 scripts 打包的先例）。业务逻辑一律在 src/，这里只做
# 参数解析 / 输出格式化 / 入口转发——本包下一律不进 pytest（手动调用验证）。
#
# 模块：
#   cli.py     统一入口（browse/detail 只读 + chat 转发），console script 指向 scripts.cli:main
#   chat/      对话调试入口包（app/prompt/render，见 docs/scripts/chat/）
#   im_server.py  IM 网关启动脚本（python scripts/im_server.py 直跑，不经本入口）

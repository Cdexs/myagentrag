# tests/ — 单元与模块测试

约定（AGENT.md 7.8）：
- 新版开发完成后，先自动化执行：新功能测试 + 修改/优化测试 + 回归测试
- 全部通过后才交付用户端侧验证
- 开发与测试只在项目环境内执行，严禁动到本地用户安装副本

命名：test_<模块>.py（如 test_workspace.py / test_slicing.py / test_extractors.py）
运行：py -3 -m pytest tests/ -v （或直接 py -3 tests/test_xxx.py）

# fake-track

fake-track 是一个用于校园跑接口链路测试的 CLI 工具。它会按小程序接口顺序执行登录、建单、检查、提交、分批轨迹上传，并支持基于本地 OSM 路网生成轨迹。

## 免责声明

本项目仅用于接口调试、协议分析和研究学习。请遵守学校与平台规则，勿用于违规用途。

## 主要能力

- 完整流程测试：login -> randrunInfo -> createLine -> checkRecord -> updateRecordNew -> uploadPathPointV3
- 路网优先轨迹生成（基于 map.osm）
- 坐标桥接：路网计算使用 WGS84，上传前自动转换回 GCJ02
- 固定 50 点分批上传，带上传进度日志
- 细粒度调试开关：可单独控制跳过等待、强制提交、导出轨迹叠加图

## 环境要求

- Python >= 3.14

## 安装

推荐使用 uv：

```bash
uv sync
```

## 快速开始

1. 复制环境变量模板

```bash
copy .env.example .env
```

2. 编辑 .env，至少填写这三项：

```env
FAKE_TRACK_KEY=
FAKE_TRACK_PHONE=
FAKE_TRACK_PASSWORD=
```

3. 运行

```bash
uv run fake-track run
```

## CLI 用法

完整流程：

```bash
uv run fake-track run
```

常用参数：

- --json-output：输出完整 JSON 报告，并关闭过程日志
- --track-image：输出轨迹叠加图到 .local/debug-images
- --track-image-path PATH：输出轨迹叠加图到指定路径
- --skip-wait：不等待模拟跑步时长，直接进入提交
- --force-submit：checkRecord 不通过也继续提交
- --ignore-target-met：即使当前次数目标已完成也继续跑

连通性检查（登录、取点、建单）：

```bash
uv run fake-track doctor
```

文本加密：

```bash
uv run fake-track encrypt "hello"
```

encrypt 只需要 `FAKE_TRACK_KEY` / `FAKE_TRACK_SECRET`，不要求配置手机号和密码。

## 调试开关

运行：

```bash
uv run fake-track run --track-image --skip-wait
```

行为：

- --track-image：自动输出轨迹叠加图到 .local/debug-images/track-overlay-<timestamp>.png
- --skip-wait：不等待模拟跑步时长，直接进入提交
- --force-submit：checkRecord 不通过也继续提交
- --ignore-target-met：忽略“当前次数目标已完成”的跳过逻辑

## 环境变量说明

必填：

- FAKE_TRACK_KEY：AES key，长度必须是 16/24/32 字节
- FAKE_TRACK_PHONE：手机号
- FAKE_TRACK_PASSWORD：密码

常用可选项：

- FAKE_TRACK_START_LAT
- FAKE_TRACK_START_LNG
- FAKE_TRACK_TARGET_DISTANCE_KM
- FAKE_TRACK_TARGET_PACE_MIN_PER_KM
- FAKE_TRACK_TARGET_DURATION_MIN_SEC
- FAKE_TRACK_TARGET_DURATION_MAX_SEC
- FAKE_TRACK_ROAD_ROUTING_ENABLED
- FAKE_TRACK_ROAD_MAP_PATH
- FAKE_TRACK_ROAD_SNAP_MAX_M
- FAKE_TRACK_ROAD_COORDINATE_BRIDGE_ENABLED
- FAKE_TRACK_REPORT_PATH

全部示例见 .env.example。

## 输出说明

- 控制台会打印阶段日志、长任务进度条和 Run Summary
- 提交前模拟等待和路径分批上传会使用 Rich 进度条
- --json-output 只输出完整 JSON 报告，不输出阶段日志、表格或进度条
- 若设置 FAKE_TRACK_REPORT_PATH，会写入完整 JSON 报告
- 使用 --track-image 会额外输出轨迹叠加图
- GitHub Actions 使用 --json-output 生成 run-report.json，并只把安全摘要写入 Step Summary

## 开发

安装开发依赖：

```bash
uv sync --group dev
```

格式化：

```bash
prek fmt
```

测试：

```bash
prek test
```

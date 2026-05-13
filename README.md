# fake-track

![Python](https://img.shields.io/badge/python-3.14%2B-blue)
![uv](https://img.shields.io/badge/package%20manager-uv-654ff0)
![Typer](https://img.shields.io/badge/CLI-Typer-0f766e)

fake-track 是一个用于校园跑接口链路测试的 CLI 工具。它会按小程序接口顺序执行登录、建单、检查、提交和分批轨迹上传；轨迹可基于本地 `map.osm` 路网生成，并在上传前完成 WGS84/GCJ02 坐标转换。

> [!IMPORTANT]
> 本项目仅用于接口调试、协议分析和研究学习。请遵守学校与平台规则，勿用于违规用途。

## 主要能力

- 全链路测试：`login -> randrunInfo -> createLine -> checkRecord -> updateRecordNew -> uploadPathPointV3`
- 路网优先轨迹生成，支持基于 `map.osm` 的道路吸附与坐标桥接
- 固定 50 点分批上传，带 Rich 阶段日志、进度条和运行摘要
- 支持 JSON 报告、轨迹叠加图导出、跳过等待、强制提交和目标次数忽略
- GitHub Actions 定时运行，目标次数已满时自动跳过

## 环境要求

- Python >= 3.14
- uv

## 快速开始

```powershell
copy .env.example .env
# Optional: copy fake-track.example.toml fake-track.toml
uv sync
uv run fake-track run
```

运行前至少填写 `.env` 中的配置：

```env
FAKE_TRACK_KEY=
FAKE_TRACK_PHONE=
FAKE_TRACK_PASSWORD=
```

`FAKE_TRACK_KEY` 也兼容旧名 `FAKE_TRACK_SECRET`，长度必须是 16/24/32 字节。
如果使用 `fake-track.toml` 的 `[[accounts]]` 配置多账号，只需要设置 `FAKE_TRACK_KEY`。

## CLI 用法

| 命令 | 用途 |
| --- | --- |
| `uv run fake-track run` | 执行一次完整链路测试 |
| `uv run fake-track counts` | 查看当前晨跑、普通跑和有效完成次数 |
| `uv run fake-track doctor` | 检查登录、取点和建单连通性 |
| `uv run fake-track encrypt "hello"` | 使用小程序同格式输出密文 |

常用参数：

| 参数 | 适用命令 | 说明 |
| --- | --- | --- |
| `--json-output` | `run`, `counts`, `doctor` | 输出 JSON；`run`/`doctor` 会关闭过程日志和进度条 |
| `--track-image` | `run` | 输出轨迹叠加图到 `.local/debug-images` |
| `--track-image-path PATH` | `run` | 输出轨迹叠加图到指定路径 |
| `--report-path PATH` | `run` | 保留控制台日志，同时把完整 JSON 报告写入文件 |
| `--skip-wait` | `run` | 不等待模拟跑步时长，直接提交 |
| `--force-submit` | `run` | `checkRecord` 不通过时仍继续提交 |
| `--ignore-target-met` | `run` | 当前次数目标已完成时仍继续跑 |
| `--account NAME_OR_INDEX` | `run`, `counts`, `doctor` | 选择指定账号（可重复传入） |

`encrypt` 只需要 `FAKE_TRACK_KEY` / `FAKE_TRACK_SECRET`，不要求配置手机号和密码。

## 配置

### 环境变量

`.env` 只放身份和密钥：

| 变量 | 说明 |
| --- | --- |
| `FAKE_TRACK_KEY` | AES key，也兼容旧名 `FAKE_TRACK_SECRET` |
| `FAKE_TRACK_PHONE` | 手机号（多账号时可省略） |
| `FAKE_TRACK_PASSWORD` | 密码（多账号时可省略） |

可选开关：

| 变量 | 说明 |
| --- | --- |
| `FAKE_TRACK_IGNORE_TARGET_MET` | 等价 `--ignore-target-met`，接受 `1/true/yes/on` |

### 多账号

多账号可以放在 `fake-track.toml` 的 `[[accounts]]` 中，手机号和密码从这里读取：

```toml
[[accounts]]
name = "account-1"
phone = ""
password = ""

[[accounts]]
name = "account-2"
phone = ""
password = ""
```

`FAKE_TRACK_KEY` 仍然从环境变量读取，所有账号共用。
`run` 默认并行执行所有账号，使用 `--account` 可选择单个账号。

### 运行参数

普通运行配置放 `fake-track.toml`。没有这个文件时会使用内置默认值；需要调整时可复制示例：

```powershell
copy fake-track.example.toml fake-track.toml
```

常用 TOML 配置：

| Section | 字段 | 说明 |
| --- | --- | --- |
| `[run]` | `start_lat`, `start_lng` | 起点坐标 |
| `[run]` | `target_distance_km`, `target_pace_min_per_km` | 目标距离和配速 |
| `[run]` | `target_duration_min_sec`, `target_duration_max_sec` | 目标耗时区间 |
| `[route]` | `road_routing_enabled`, `road_map_path` | 是否启用路网和 OSM 地图路径 |
| `[route]` | `road_snap_max_m`, `road_coordinate_bridge_enabled` | 路网吸附和坐标桥接 |
| `[output]` | `report_path` | JSON 报告输出路径 |

更细的轨迹采样和 guard 参数见 [fake-track.example.toml](fake-track.example.toml)。

## 输出

- 默认输出阶段日志、长任务进度条和 `Run Summary`
- `--json-output` 仅输出完整 JSON，适合脚本消费
- `--report-path PATH` 会保留正常控制台输出，并额外写出完整 JSON，适合 CI
- `fake-track.toml` 里的 `[output].report_path` 会额外写入完整 JSON 报告
- `--track-image` 会生成 `.local/debug-images/track-overlay-<timestamp>.png`

## GitHub Actions

[Daily Campus Run](.github/workflows/daily-run.yml) 会在 Asia/Shanghai 时区每天 06:05 和 14:00 运行，也支持手动触发。需要配置以下仓库 Secrets：

- `FAKE_TRACK_KEY`
- `FAKE_TRACK_PHONE`
- `FAKE_TRACK_PASSWORD`

如果想通过仓库 Variables 控制忽略目标次数，可设置：

- `FAKE_TRACK_IGNORE_TARGET_MET`（建议值：`true`）

工作流使用 `--report-path run-report.json` 保留运行过程日志，同时生成 JSON 报告并把安全摘要写入 Step Summary。目标次数已满会视为成功跳过，其他异常跳过或上传批次数为 0 会失败。

## 开发

```powershell
uv sync --group dev
prek fmt
prek test
```

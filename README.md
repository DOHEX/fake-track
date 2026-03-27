# fake-track

一个用于华东理工大学校园跑系统的自动化测试工具，可以生成模拟跑步轨迹并上传到服务器。

## 功能特性

- 生成逼真的人类跑步轨迹
- 支持全模式和连接性测试模式
- 自动登录和数据上传
- 加密数据传输
- 丰富的配置选项

## 安装

### 系统要求

- Python >= 3.14

### 安装步骤

1. 克隆仓库：
```bash
git clone https://github.com/your-username/fake-track.git
cd fake-track
```

2. 安装依赖：
```bash
pip install -e .
```

## 使用方法

### 基本使用

运行一次完整的测试周期：
```bash
fake-track run
```

运行连接性测试：
```bash
fake-track run --mode connectivity
```

### 命令选项

- `--force`: 即使 checkRecord 报告状态为 0 也继续
- `--quiet`: 禁用进度日志，只输出最终结果
- `--json-output`: 输出完整的 JSON 报告而不是摘要

### 加密文本

使用与小程序相同的 IV:密文 格式加密文本：
```bash
fake-track encrypt "your_text_here"
```

## 配置

通过环境变量进行配置：

### 必需配置

- `PHONE`: 登录手机号
- `PASSWORD`: 登录密码
- `RUN_KEY`: 运行密钥（用于加密）

### 可选配置

#### 地理位置设置
- `START_LAT`: 起始纬度（默认：30.83378）
- `START_LNG`: 起始经度（默认：121.504532）

#### 目标参数
- `TARGET_DISTANCE_KM`: 目标距离（公里，默认：2.03）
- `TARGET_PACE_MIN_PER_KM`: 目标配速（分钟/公里，默认：6.0）
- `TARGET_DURATION_MIN_SEC`: 最小持续时间（秒，默认：460）
- `TARGET_DURATION_MAX_SEC`: 最大持续时间（秒，默认：490）

#### 轨迹生成参数
- `SAMPLE_INTERVAL_SEC`: 采样间隔（秒，默认：1）
- `MUST_PASS_RADIUS_KM`: 必经点半径（公里，默认：0.05）
- `COMPENSATION_FACTOR`: 补偿因子（默认：1.0）
- `POINT_ACCURACY_MIN`: 最小点精度（默认：8）
- `POINT_ACCURACY_MAX`: 最大点精度（默认：25）
- `POINT_JITTER_M`: 点抖动（米，默认：2.5）
- `TIMESTAMP_JITTER_MS`: 时间戳抖动（毫秒，默认：220）

#### 网络设置
- `TIMEOUT_SEC`: 超时时间（秒，默认：20）
- `RETRY_COUNT`: 重试次数（默认：3）

### 环境文件

你可以在项目根目录创建 `.env` 文件来设置环境变量：

```bash
# .env
PHONE=your_phone_number
PASSWORD=your_password
RUN_KEY=your_run_key
```

## 开发

### 开发依赖

安装开发依赖：
```bash
pip install -e ".[dev]"
```

### 代码格式化

使用 ruff 进行代码格式化：
```bash
prek fmt
```

### 运行测试

```bash
prek test
```

## 许可证

本项目仅供学习和研究使用，请勿用于任何非法目的。

## 作者

DOHEX <dohex@outlook.com>
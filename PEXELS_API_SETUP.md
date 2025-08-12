# Pexels API 设置说明

## 获取 Pexels API 密钥

1. 访问 [Pexels API](https://www.pexels.com/api/)
2. 注册一个免费账户
3. 在开发者页面获取你的 API 密钥

## 设置环境变量

### 方法1: 创建 .env 文件
在项目根目录创建 `.env` 文件，添加以下内容：

```
PEXELS_API_KEY=your-pexels-api-key-here
```

### 方法2: 设置系统环境变量
在 Windows PowerShell 中运行：

```powershell
$env:PEXELS_API_KEY="your-pexels-api-key-here"
```

### 方法3: 在代码中直接设置
在 `app/views.py` 文件中，将以下行：
```python
pexels_api_key = os.environ.get('PEXELS_API_KEY', '')
```

修改为：
```python
pexels_api_key = 'your-pexels-api-key-here'
```

## API 功能

- **免费额度**: 每月 200 次请求
- **图片质量**: 高质量、专业摄影作品
- **图片尺寸**: 自动获取适合的尺寸
- **备用方案**: 如果没有 API 密钥，会自动使用 Unsplash API

## 支持的图片类别

- nature (自然)
- abstract (抽象)
- minimal (极简)
- gradient (渐变)
- geometric (几何)

## 注意事项

1. API 密钥是免费的，但每月有请求限制
2. 建议在生产环境中使用环境变量存储 API 密钥
3. 如果 API 调用失败，系统会自动使用 Unsplash 作为备用方案 
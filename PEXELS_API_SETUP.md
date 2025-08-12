# Pexels API Setup Instructions

## Get Pexels API Key

1. Visit [Pexels API](https://www.pexels.com/api/)
2. Register a free account
3. Get your API key from the developer page

## Set Environment Variables

### Method 1: Create .env file
Create a `.env` file in the project root directory and add the following content:

```
PEXELS_API_KEY=your-pexels-api-key-here
```

### Method 2: Set System Environment Variables
Run in Windows PowerShell:

```powershell
$env:PEXELS_API_KEY="your-pexels-api-key-here"
```

### Method 3: Set directly in code
In the `app/views.py` file, change the following line:
```python
pexels_api_key = os.environ.get('PEXELS_API_KEY', '')
```

To:
```python
pexels_api_key = 'your-pexels-api-key-here'
```

## API Features

- **Free Quota**: 200 requests per month
- **Image Quality**: High-quality, professional photography
- **Image Sizes**: Automatically get suitable sizes
- **Fallback**: If no API key, automatically use Unsplash API

## Supported Image Categories

- nature (Nature)
- abstract (Abstract)
- minimal (Minimal)
- gradient (Gradient)
- geometric (Geometric)

## Notes

1. API key is free but has monthly request limits
2. It's recommended to use environment variables to store API keys in production
3. If API calls fail, the system will automatically use Unsplash as fallback 
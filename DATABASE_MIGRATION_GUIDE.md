# 数据库迁移使用指南

## 概述
DailyDot应用现在使用Flask-Migrate来管理数据库结构变更，这样可以安全地更新数据库而不丢失数据。

## 快速开始

### 1. 初始化迁移（仅需运行一次）
```bash
python manage_db.py init
```

### 2. 应用现有迁移
```bash
python manage_db.py upgrade
```

## 日常使用

### 查看迁移状态
```bash
python manage_db.py status
```

### 创建新迁移
当您修改了模型（models.py）后，需要创建新的迁移：
```bash
python manage_db.py migrate "描述你的变更"
```
例如：
```bash
python manage_db.py migrate "Add user preferences field"
```

### 应用新迁移
创建迁移后，需要应用它：
```bash
python manage_db.py upgrade
```

## 开发环境

### 重置数据库（仅开发时使用）
⚠️ **警告：这会删除所有数据！**
```bash
python manage_db.py reset
```

## 生产环境

在生产环境中，您应该：

1. **备份数据库**：在应用迁移前备份数据
2. **测试迁移**：在测试环境中先测试迁移
3. **应用迁移**：使用 `python manage_db.py upgrade`

## 迁移文件

迁移文件存储在 `migrations/versions/` 目录中，每个文件包含：
- 升级操作（upgrade）：应用变更
- 降级操作（downgrade）：回滚变更

## 常见问题

### Q: 迁移失败怎么办？
A: 检查错误信息，可能需要手动修复数据库或回滚到上一个版本。

### Q: 如何回滚迁移？
A: 使用 `flask db downgrade` 命令。

### Q: 迁移文件冲突怎么办？
A: 删除冲突的迁移文件，重新创建迁移。

## 最佳实践

1. **经常创建迁移**：每次修改模型后立即创建迁移
2. **描述性消息**：使用清晰的描述说明迁移内容
3. **测试迁移**：在生产环境应用前先测试
4. **备份数据**：重要数据变更前备份数据库

## 命令总结

| 命令 | 用途 | 使用频率 |
|------|------|----------|
| `init` | 初始化迁移系统 | 一次性 |
| `upgrade` | 应用迁移 | 经常 |
| `migrate` | 创建新迁移 | 修改模型后 |
| `status` | 查看状态 | 需要时 |
| `reset` | 重置数据库 | 仅开发时 | 
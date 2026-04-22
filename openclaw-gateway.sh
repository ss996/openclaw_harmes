#!/usr/bin/env bash
# OpenClaw Gateway 控制脚本：关闭 / 启动 / 重启

set -e

CMD="${1:-}"

usage() {
  echo "用法: $0 <stop|start|restart|status>"
  echo ""
  echo "  stop    - 停止网关服务"
  echo "  start   - 启动网关服务（安装并启动 LaunchAgent）"
  echo "  restart - 先停止再启动"
  echo "  status  - 查看网关运行状态"
  echo ""
  echo "示例:"
  echo "  $0 restart"
  echo "  $0 status"
}

case "$CMD" in
  stop)
    echo "正在停止 OpenClaw Gateway..."
    openclaw gateway stop
    echo "已停止。"
    ;;
  start)
    echo "正在启动 OpenClaw Gateway..."
    openclaw gateway install
    echo "已启动。Dashboard: http://127.0.0.1:18789/"
    ;;
  restart)
    echo "正在重启 OpenClaw Gateway..."
    openclaw gateway stop
    sleep 2
    openclaw gateway install --force
    echo "已重启。Dashboard: http://127.0.0.1:18789/"
    ;;
  status)
    openclaw gateway status
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "错误: 未知命令 '$CMD'"
    usage
    exit 1
    ;;
esac

// 学迹分析 · 微信小程序入口
App({
  globalData: {
    // API 地址：开发时指向本地，上线后替换
    apiBase: 'http://localhost:8000/api/v1',
    studentId: 'student_001',
    subject: '数学',
  },

  onLaunch() {
    // 检查登录态等（后续扩展）
    console.log('学迹分析启动');
  },
});
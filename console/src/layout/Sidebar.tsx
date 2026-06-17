/** Sidebar navigation menu with magnetic icon hover effects. */

import { Menu } from 'antd';
import {
  DashboardOutlined,
  MessageOutlined,
  BookOutlined,
  EditOutlined,
  FundOutlined,
  LikeOutlined,
  CheckCircleOutlined,
  RocketOutlined,
  AuditOutlined,
  FileSearchOutlined,
  ExperimentOutlined,
  AlertOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import Magnet from '@/components/react-bits/Magnet';

const menuItems = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/pilot', icon: <RocketOutlined />, label: 'Pilot 指标' },
  { key: '/conversations', icon: <MessageOutlined />, label: '对话记录' },
  { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
  { key: '/prompts', icon: <EditOutlined />, label: 'Prompt 管理' },
  { key: '/workflow', icon: <FundOutlined />, label: '工作流质量' },
  { key: '/feedback', icon: <LikeOutlined />, label: '反馈管理' },
  { key: '/review', icon: <AuditOutlined />, label: '质量审查' },
  { key: '/gaps', icon: <FileSearchOutlined />, label: '知识缺口' },
  { key: '/eval', icon: <ExperimentOutlined />, label: 'Eval 回归' },
  { key: '/alerts', icon: <AlertOutlined />, label: '运维告警' },
  { key: '/reports', icon: <FileTextOutlined />, label: 'Pilot 报告' },
  { key: '/readiness', icon: <CheckCircleOutlined />, label: 'Pilot 就绪' },
].map((item) => ({
  ...item,
  icon: (
    <Magnet padding={20} strength={3}>
      {item.icon}
    </Magnet>
  ),
}));

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();

  // Find the matching top-level key
  const selectedKey = menuItems.find((item) => location.pathname.startsWith(item.key))?.key || '/dashboard';

  return (
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      items={menuItems}
      onClick={({ key }) => navigate(key)}
      style={{ height: '100%', borderRight: 0 }}
    />
  );
}

/** Confirmation modal wrapper for destructive actions. */

import { Modal } from 'antd';
import { ExclamationCircleOutlined } from '@ant-design/icons';

interface Props {
  title: string;
  description: string;
  onConfirm: () => void | Promise<void>;
  okText?: string;
  danger?: boolean;
}

export default function confirmAction({ title, description, onConfirm, okText = '确认', danger = true }: Props) {
  Modal.confirm({
    title,
    icon: <ExclamationCircleOutlined />,
    content: description,
    okText,
    okType: danger ? 'danger' : 'primary',
    cancelText: '取消',
    onOk: onConfirm,
  });
}

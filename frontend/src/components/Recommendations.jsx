import React, { useEffect, useState } from 'react';
import { Card, Empty, List, Space, Spin, Tag, Typography } from 'antd';
import { getReportRecommendations } from '../api.js';

// 为空的三种情况必须能区分：把"商城不可达"和"没有对应商品"合并成一句，
// 会让运维分不清该去修配置还是该去提醒租户上货。
const EMPTY_REASONS = {
  no_published_card: '本次未能生成健康风险提示，因此没有可对应的商品。',
  no_labels: '该健康方向尚未配置对应的商城商品标签。',
  no_label_data: '该健康方向暂无可推荐的已上架商品。',
  mall_unavailable: '商城暂时不可用，请稍后再试。',
};

function priceText(item) {
  if (item.price_down && item.price_up && item.price_down !== item.price_up) {
    return `¥${item.price_down} - ¥${item.price_up}`;
  }
  const single = item.price_down || item.price_up;
  return single ? `¥${single}` : '价格未标注';
}

function stockTag(stock) {
  // 商城的 null 是"未标注"，不是 0。渲染成 0 或"缺货"都是伪造。
  if (stock === null || stock === undefined) {
    return <Tag>库存未标注</Tag>;
  }
  return <Tag color={stock > 0 ? 'green' : 'default'}>{stock > 0 ? `库存 ${stock}` : '缺货'}</Tag>;
}

function GoodsCard({ item }) {
  return (
    <List.Item className="recommendation-item">
      {item.image ? (
        <img className="recommendation-image" src={item.image} alt={item.name || '推荐商品'} />
      ) : (
        <div className="recommendation-image recommendation-image-missing">暂无图片</div>
      )}
      <div className="recommendation-body">
        <Typography.Text strong>{item.name || '未命名商品'}</Typography.Text>
        <Space wrap size={4} style={{ marginTop: 6 }}>
          <Typography.Text type="danger">{priceText(item)}</Typography.Text>
          {stockTag(item.stock)}
        </Space>
      </div>
    </List.Item>
  );
}

export default function Recommendations({ reportId, reportToken = '' }) {
  const [state, setState] = useState({ loading: true, items: [], reason: null, error: '' });

  useEffect(() => {
    if (!reportId) {
      setState({ loading: false, items: [], reason: 'no_published_card', error: '' });
      return undefined;
    }
    let active = true;
    setState({ loading: true, items: [], reason: null, error: '' });
    getReportRecommendations(reportId, reportToken)
      .then((data) => {
        if (active) {
          setState({ loading: false, items: data.items || [], reason: data.reason || null, error: '' });
        }
      })
      .catch((err) => {
        // 取推荐失败不阻断报告页：如实说明，不伪造商品。
        if (active) {
          setState({ loading: false, items: [], reason: 'mall_unavailable', error: err.message });
        }
      });
    return () => { active = false; };
  }, [reportId, reportToken]);

  return (
    <Card className="recommendations-card" title="推荐商品" style={{ marginTop: 16 }}>
      {state.loading ? (
        <div className="recommendations-loading"><Spin /></div>
      ) : state.items.length > 0 ? (
        <List
          className="recommendation-list"
          grid={{ gutter: 16, xs: 1, sm: 2, md: 3 }}
          dataSource={state.items}
          renderItem={(item) => <GoodsCard item={item} />}
        />
      ) : (
        <>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无推荐"
          />
          <Typography.Paragraph type="secondary" style={{ textAlign: 'center', marginBottom: 0 }}>
            {EMPTY_REASONS[state.reason] || '该报告暂无可推荐的商品。'}
          </Typography.Paragraph>
        </>
      )}
    </Card>
  );
}

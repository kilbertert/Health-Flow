import React, { useEffect, useState } from 'react';
import { Button, Card, Empty, List, Space, Spin, Tag, Typography, message } from 'antd';
import { createCartLink, getReportRecommendations } from '../api.js';

// 为空的三种情况必须能区分：把"商城不可达"和"没有对应商品"合并成一句，
// 会让运维分不清该去修配置还是该去提醒租户上货。
// 后端的三种 reason 由服务端给出；`session_expired` 是**客户端**的第四种，
// 不对应服务端契约——它描述的是这次请求根本没到达取商品那一步。
const EMPTY_REASONS = {
  no_published_card: '本次未能生成健康风险提示，因此没有可对应的商品。',
  no_label_data: '该健康方向暂无可推荐的已上架商品。',
  mall_unavailable: '商城暂时不可用，请稍后再试。',
  session_expired: '登录状态已失效，请重新登录后查看推荐。',
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

function GoodsCard({ item, onAddToCart, busy }) {
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
        <Button
          type="primary"
          size="small"
          style={{ marginTop: 8 }}
          loading={busy}
          onClick={() => onAddToCart(item)}
        >
          加入购物车
        </Button>
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
        // 但**不能把所有失败都归给商城**：会话过期（或报告被删除后重开）会让这个请求
        // 拿到 401/404，那是本服务自己的访问问题，说成"商城不可用"会把人引到错的排查方向。
        if (active) {
          const expired = /\b(401|404)\b/.test(err.message || '');
          setState({
            loading: false,
            items: [],
            reason: expired ? 'session_expired' : 'mall_unavailable',
            error: err.message,
          });
        }
      });
    return () => { active = false; };
  }, [reportId, reportToken]);

  const [busySpu, setBusySpu] = useState('');

  const addToCart = async (item) => {
    setBusySpu(item.id);
    try {
      const link = await createCartLink(reportId, item.id, reportToken);
      if (link.url) {
        window.location.assign(link.url);
        return;
      }
      // 服务端明确说构造不出来——如实说明是**跳转前**的失败，与「商城拒绝了链接」
      // 是两回事，后者本仓看不到（契约里分得很清）。
      message.info('暂时无法跳转到商城，请稍后再试。');
    } catch (err) {
      message.error(err.message || '暂时无法跳转到商城，请稍后再试。');
    } finally {
      setBusySpu('');
    }
  };

  return (
    <Card className="recommendations-card" title="推荐商品" style={{ marginTop: 16 }}>
      {state.loading ? (
        <div className="recommendations-loading"><Spin /></div>
      ) : state.items.length > 0 ? (
        <List
          className="recommendation-list"
          grid={{ gutter: 16, xs: 1, sm: 2, md: 3 }}
          dataSource={state.items}
          renderItem={(item) => (
            <GoodsCard item={item} onAddToCart={addToCart} busy={busySpu === item.id} />
          )}
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

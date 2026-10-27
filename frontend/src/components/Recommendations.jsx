import React, { useEffect, useState } from 'react';
import { Button, Card, Empty, List, Space, Spin, Tag, Typography, message } from 'antd';
import { createCartLink, getReportRecommendations } from '../api.js';

// 取货为空的原因由**服务端**给出（`app/service/recommendation_empty_reason.py` 是词表的
// 家）。这里只把每个取值翻成患者能懂的一句话 —— **不推导、不合并、不发明**。
//
// 这里此前有两处客户端自造的事实，本文件不再有：
//   1. `session_expired` —— 一个服务端**从不产生**的第四种原因，靠 `/\\b(401|404)\\b/`
//      从**人话**里捞 HTTP 状态。那句人话由 `api.js` 的 `parseError` 拼成，可能被服务端的
//      `detail` 覆盖，于是同一个 401 在有的路径上被认成「登录失效」、在有的路径上被认成
//      「商城暂时不可用」。现在访问失败由服务端具名出域（响应头 `X-Report-Access-Reason`），
//      前端读**结构化事实**。
//   2. 「加购失败」那句一概而论的兜底 —— 服务端已经把 `report_not_ready` 与
//      `link_unavailable` 两个具名原因送过来了，丢掉它们等于把一个到手的结论扔掉。
export const EMPTY_REASONS = {
  no_published_card: '本次未能生成健康风险提示，因此没有可对应的商品。',
  no_label_data: '该健康方向暂无可推荐的已上架商品。',
  mall_unavailable: '商城暂时不可用，请稍后再试。',
  // 没有对当前版本的「商品推荐」同意（#271）。它与「商城没货」是两件事：患者没允许
  // 我们拿他的报告去推荐，所以这句话要指向**开启推荐**那个动作，而不是「稍后再试」。
  product_recommendation_consent_required: '尚未开启商品推荐授权，开启后即可看到推荐商品。',
};

// 报告访问那一类：这份报告读不到（会话失效，或报告不存在/不属于你）。它们是**报告级访问
// 问题**，不是「商城的空」—— 说成「商城不可用」会把人引到错的排查方向。
//
// 会话失效那句必须指向**商城入口**：本仓没有登录页（#172），叫患者「重新登录」是在指向
// 一个不存在的页面。这句话的正确来源也是服务端（`app/api/auth.py`），这里与它同向。
export const ACCESS_REASONS = {
  session_expired: '登录状态已失效，请从商城入口重新进入后查看推荐。',
  report_not_found: '这份报告打不开，请返回报告列表重新进入。',
  // 没有对当前版本的「报告解读」同意（#269）。它**不**是「商城不可用」或「稍后再试」：
  // 患者要做的是去完成授权，所以这句话指向那个动作。
  report_interpretation_consent_required: '请先完成「报告解读」授权，再查看报告与推荐。',
};

// 加购失败的两个具名原因（服务端 `CartLinkResponse.reason`）。两件事、两句话。
export const CART_LINK_REASONS = {
  report_not_ready: '这份报告还没完成确认，暂时不能加购。',
  link_unavailable: '暂时无法跳转到商城，请稍后再试。',
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
      // 报告都还没打开，谈不上「没有可据以取货的风险」——**不伪造服务端的成因**。
      // 组件本不该在这个状态下挂载（调用方都在拿到报告之后才渲染它）；真的出现时
      // 如实说「还没有报告」，而不是冒充一个服务端取值。
      setState({ loading: false, items: [], reason: null, error: '' });
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
        //
        // 归因不再靠正则从消息里捞状态码 —— 那是「按自由文本决定归因」，与服务端那句
        // 人话的措辞耦合（#226 消灭的是同一形状的错法）。起因由服务端**具名**出域
        // （响应头 `X-Report-Access-Reason`），拿不到时才落到中性的「商城不可用」。
        if (active) {
          setState({
            loading: false,
            items: [],
            reason: err.accessReason || 'mall_unavailable',
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
      // 服务端给了**具名原因**（`report_not_ready` / `link_unavailable`），照它说话。
      // 此前这里是一句一概而论的「暂时无法跳转到商城」—— 把两个已经到手的结论扔掉了。
      message.info(CART_LINK_REASONS[link.reason] || '暂时无法跳转到商城，请稍后再试。');
    } catch (err) {
      message.error(CART_LINK_REASONS[err.accessReason] || err.message || '暂时无法跳转到商城，请稍后再试。');
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
            {state.reason
              ? EMPTY_REASONS[state.reason] || ACCESS_REASONS[state.reason] || '该报告暂无可推荐的商品。'
              : '该报告暂无可推荐的商品。'}
          </Typography.Paragraph>
        </>
      )}
    </Card>
  );
}

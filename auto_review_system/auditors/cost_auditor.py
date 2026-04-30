def audit_cost(excel_data):
    """
    基于基础规则的成本审核逻辑
    :param excel_data: excel_parser 输出的数据格式
    :return: 审核意见列表
    """
    issues = []
    
    # Example Rules
    price_thresholds = {
        '外墙涂料': 80.0,
        '防水卷材': 65.0,
        'EPS': 0, # 禁止使用
    }
    
    for sheet in excel_data:
        sheet_name = sheet['sheet_name']
        for item in sheet['items']:
            name = item['name']
            price = item['price']
            qty = item['quantity']
            
            # 1. 简单限价校验
            for keyword, max_price in price_thresholds.items():
                if keyword in name:
                    if max_price == 0:
                        issues.append({
                            'level': 'RED',
                            'title': '【严重违规】禁止使用材料',
                            'detail': f"清单 {sheet_name} 中包含了违规材料 '{name}'，违反《万科红线底线》禁用原则。"
                        })
                    elif price > max_price:
                        issues.append({
                            'level': 'YELLOW',
                            'title': '【成本超标】单价高于限价库',
                            'detail': f"清单 {sheet_name} 中 '{name}' 单价为 {price} 元，超过企业指导价 {max_price} 元。"
                        })
                        
            # 2. 算术错误（如果需要，可以在 parser 阶断提取总价进行校验）
            # 当前未提取用户填报的合价，为了 MVP 演示，我们只验证单价基准
            
    return issues

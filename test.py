from xtquant import xtdata

xtdata.download_his_st_data() # 下载历史ST数据/

def check_date_status(stock_code,date_str):
    """
    ToDo:
        检查给定日期是否在ST或*ST的日期范围内
    Args:
        stock_code: 股票代码
        date_str: 要检查的日期，格式为"YYYYMMDD"
    Return: 
        找到的状态('ST'或'*ST')，如果未找到，返回None
    """
    data = xtdata.get_his_st_data(stock_code)
    # 遍历字典中的每个状态和对应的日期范围列表
    for status, ranges in data.items():
        for start_date, end_date in ranges:
            # 检查给定的日期是否在当前的日期范围内
            if start_date <= date_str <= end_date:
                return status  # 返回找到的状态
    return None  # 如果未找到，返回None  
if __name__ == "__main__":
    check_date_status("000004.SZ","20110228")
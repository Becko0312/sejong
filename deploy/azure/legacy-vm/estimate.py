"""Read-only Azure retail-price lookup; no cloud account or resources needed."""
import json
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timezone

region = 'koreacentral'
filters = {
    'vm': f"armRegionName eq '{region}' and armSkuName eq 'Standard_B2als_v2' and priceType eq 'Consumption'",
    'disk': f"armRegionName eq '{region}' and serviceName eq 'Storage' and contains(skuName, 'E6 LRS') and priceType eq 'Consumption'",
    'ip': f"armRegionName eq '{region}' and serviceName eq 'Virtual Network' and contains(productName, 'IP Addresses') and priceType eq 'Consumption'",
}
result = {'retrieved_at': datetime.now(timezone.utc).isoformat(), 'region': region, 'currency': 'USD', 'meters': {}}
for kind, query in filters.items():
    url = 'https://prices.azure.com/api/retail/prices?' + urllib.parse.urlencode({'$filter': query})
    items = []
    while url:
        with urllib.request.urlopen(url, timeout=60) as response:
            page = json.load(response)
        items.extend(page['Items'])
        url = page.get('NextPageLink')
    result['meters'][kind] = [{k: item.get(k) for k in ['productName', 'skuName', 'meterName', 'retailPrice', 'unitOfMeasure', 'effectiveStartDate']} for item in items]
path = Path(__file__).with_name('cost-estimate.json')
path.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))

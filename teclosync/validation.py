class ValidationError(Exception):
    pass

def validate_refresh(database, sources, refresh_result):
    source_results = {item['source_id']: item for item in refresh_result['sources']}
    healthy = []
    failed = []
    for source in sources:
        result = source_results.get(source['id'], {'status': 'missing', 'record_count': 0, 'reason': 'missing_result'})
        count = database.source_channel_count(source['id'])
        entry = {'source_id': source['id'], 'status': result['status'], 'record_count': count, 'minimum_records': source['min_records']}
        if result['status'] == 'healthy':
            if count < source['min_records']:
                entry['status'] = 'failed'
                entry['reason'] = 'staged_count_below_minimum'
                failed.append(entry)
            else:
                healthy.append(entry)
        else:
            entry['reason'] = result.get('reason', 'unknown')
            failed.append(entry)
    total = sum(item['record_count'] for item in healthy)
    report = {'run_id': refresh_result['run_id'], 'started_at': refresh_result['started_at'], 'healthy_sources': healthy, 'failed_sources': failed, 'total_channels': total, 'publication_allowed': bool(healthy) and total > 0}
    if not healthy:
        raise ValidationError('global_source_collapse')
    return report

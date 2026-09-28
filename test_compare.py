from schedule_parser import compare_schedules

old = [{"date":"2026-09-28","slot":"1-2","subject":"Математика","teacher":"А","room":"101","start_time":"08:30","end_time":"10:00"}]
new = [{"date":"2026-09-28","slot":"1-2","subject":"Математика","teacher":"Б","room":"102","start_time":"08:30","end_time":"10:00"},
       {"date":"2026-09-29","slot":"3-4","subject":"История","teacher":"В","room":"202","start_time":"10:10","end_time":"11:40"}]
result = compare_schedules(old,new)
assert len(result['changed']) == 1
assert len(result['added']) == 1
assert len(result['removed']) == 0
print('compare test: OK')

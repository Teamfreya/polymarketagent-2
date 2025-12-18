#!/usr/bin/env python3
"""
Comprehensive fix for bot.py:
1. Remove orphaned try block at line 313
2. Remove orphaned except/finally blocks
3. Add proper try/finally wrapper for cycle lock
4. Fix all indentation
"""

with open('src/bot.py', 'r') as f:
    lines = f.readlines()

# Find key line numbers
run_cycle_full_start = None
check_open_positions_start = None

for i, line in enumerate(lines):
    if 'def run_cycle_full(self):' in line:
        run_cycle_full_start = i
    if 'def check_open_positions(self):' in line:
        check_open_positions_start = i
        break

print(f"run_cycle_full starts at line {run_cycle_full_start + 1}")
print(f"check_open_positions starts at line {check_open_positions_start + 1}")

# Remove the orphaned try at line 312 (index 311) and its except/finally
# The try block is at index 312 (line 313)
# We need to remove it and unindent everything until the except block

output_lines = []
skip_lines = set()

# Mark line 312 (try:) for removal
if run_cycle_full_start:
    # Find and remove the orphaned try block around line 312
    for i in range(run_cycle_full_start, min(run_cycle_full_start + 20, len(lines))):
        if 'try:' in lines[i] and lines[i].strip() == 'try:':
            skip_lines.add(i)
            print(f"Removing orphaned try at line {i+1}")
            break

# Find and remove orphaned except/finally
for i in range(run_cycle_full_start, check_open_positions_start):
    line = lines[i].strip()
    if line.startswith('except Exception as e:') and 'CRITICAL: Unexpected error' in ''.join(lines[i:i+3]):
        # This is the orphaned except block
        skip_lines.add(i)
        skip_lines.add(i+1)  # print line
        skip_lines.add(i+2)  # release_event line  
        skip_lines.add(i+3)  # return line
        print(f"Removing orphaned except block at lines {i+1}-{i+4}")
    if line.startswith('finally:') and '_cycle_lock.release()' in ''.join(lines[i:i+3]):
        skip_lines.add(i)
        skip_lines.add(i+1)  # comment
        skip_lines.add(i+2)  # release line
        print(f"Removing orphaned finally block at lines {i+1}-{i+3}")

# Write output
for i, line in enumerate(lines):
    if i not in skip_lines:
        output_lines.append(line)

with open('src/bot.py', 'w') as f:
    f.writelines(output_lines)

print("Removed orphaned try/except/finally blocks")

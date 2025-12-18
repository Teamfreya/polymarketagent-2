#!/usr/bin/env python3
"""Fix indentation in bot.py for the outer try/finally block"""

with open('src/bot.py', 'r') as f:
    lines = f.readlines()

# Lines 331-705 need to be indented by 4 spaces (one level)
# Line 706-709 (except block) needs to be at same level as outer try (line 313)
# Line 710-712 (finally block) needs to be at same level as outer try

output_lines = []
for i, line in enumerate(lines, 1):
    if 331 <= i <= 705:
        # Add 4 spaces of indentation
        output_lines.append('    ' + line)
    else:
        output_lines.append(line)

with open('src/bot.py', 'w') as f:
    f.writelines(output_lines)

print("Fixed indentation")

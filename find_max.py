def find_max(lst):
    if not lst:
        return None
    return max(lst)

numbers = [3, 5, 2, 8, 1]
print(find_max(numbers))
import random
import subprocess
import sys
from itertools import product

def brute_force(h, M, K):
    """Try all possible sequences of operations, return max count of K."""
    n = len(h)
    if M == 1:
        return 1
    
    # BFS/DFS over states - but states are too many for big N
    # For small N, use BFS
    from collections import deque
    
    initial = tuple(h)
    visited = {initial}
    queue = deque([initial])
    best = h.count(K)
    
    while queue:
        state = queue.popleft()
        cnt = state.count(K)
        if cnt > best:
            best = cnt
        
        # Try all windows of size M
        for i in range(n - M + 1):
            window = state[i:i+M]
            mn = min(window)
            if mn < max(window):  # operation changes something
                new_state = list(state)
                for j in range(i, i + M):
                    new_state[j] = mn
                new_state = tuple(new_state)
                if new_state not in visited:
                    visited.add(new_state)
                    queue.append(new_state)
    
    return best

def fast_solve(h_arr, queries):
    """Our fast solution logic."""
    n = len(h_arr)
    h = [0] * (n + 2)
    pos = [0] * (n + 1)
    for i in range(1, n + 1):
        h[i] = h_arr[i-1]
        pos[h[i]] = i
    
    left = [0] * (n + 2)
    stk = []
    for i in range(1, n + 1):
        while stk and h[stk[-1]] >= h[i]:
            stk.pop()
        left[i] = stk[-1] if stk else 0
        stk.append(i)
    
    right = [0] * (n + 2)
    n1 = n + 1
    stk = []
    for i in range(n, 0, -1):
        while stk and h[stk[-1]] >= h[i]:
            stk.pop()
        right[i] = stk[-1] if stk else n1
        stk.append(i)
    
    span = [0] * (n + 1)
    for k in range(1, n + 1):
        p = pos[k]
        span[k] = right[p] - left[p] - 1
    
    results = []
    for M, K in queries:
        s = span[K]
        if M > 1 and s >= M:
            results.append(s)
        else:
            results.append(1)
    return results

def stress():
    max_n = 8  # small for brute force
    num_tests = 5000
    
    for test_num in range(num_tests):
        n = random.randint(1, max_n)
        h = list(range(1, n + 1))
        random.shuffle(h)
        
        # Generate random queries
        num_queries = random.randint(1, 5)
        queries = []
        for _ in range(num_queries):
            M = random.randint(1, n)
            K = random.randint(1, n)
            queries.append((M, K))
        
        # Fast solution
        fast_results = fast_solve(h, queries)
        
        # Brute force
        for qi, (M, K) in enumerate(queries):
            bf_ans = brute_force(h, M, K)
            fast_ans = fast_results[qi]
            
            if bf_ans != fast_ans:
                print(f"MISMATCH on test {test_num}!")
                print(f"  n={n}, h={h}")
                print(f"  Query: M={M}, K={K}")
                print(f"  Brute force: {bf_ans}")
                print(f"  Fast: {fast_ans}")
                return False
    
    print(f"All {num_tests} tests passed!")
    return True

stress()

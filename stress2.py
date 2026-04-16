import random
from collections import deque
from array import array

def brute_force(h, M, K):
    n = len(h)
    if M == 1:
        return 1
    initial = tuple(h)
    visited = {initial}
    queue = deque([initial])
    best = initial.count(K)
    while queue:
        state = queue.popleft()
        cnt = state.count(K)
        if cnt > best:
            best = cnt
        for i in range(n - M + 1):
            mn = min(state[i:i+M])
            if mn < max(state[i:i+M]):
                new_state = list(state)
                for j in range(i, i + M):
                    new_state[j] = mn
                new_state = tuple(new_state)
                if new_state not in visited:
                    visited.add(new_state)
                    queue.append(new_state)
    return best

def solve_two_pass(h_list, N, queries):
    pos = [0] * (N + 1)
    for i in range(N):
        pos[h_list[i]] = i
    left = [-1] * N
    stk = []
    for i in range(N):
        while stk and h_list[stk[-1]] >= h_list[i]:
            stk.pop()
        if stk:
            left[i] = stk[-1]
        stk.append(i)
    right = [N] * N
    stk = []
    for i in range(N - 1, -1, -1):
        while stk and h_list[stk[-1]] >= h_list[i]:
            stk.pop()
        if stk:
            right[i] = stk[-1]
        stk.append(i)
    span = [0] * (N + 1)
    for k in range(1, N + 1):
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

def solve_single_pass(h_list, N, queries):
    h = array('i', h_list)
    vs = array('i', [0]) * (N + 1)
    si = array('i', [0]) * N
    sl = array('i', [0]) * N
    top = 0
    for i in range(N):
        hi = h[i]
        lv = -1
        while top > 0 and h[si[top - 1]] >= hi:
            top -= 1
            vs[h[si[top]]] = i - sl[top] - 1
        if top > 0:
            lv = si[top - 1]
        si[top] = i
        sl[top] = lv
        top += 1
    while top > 0:
        top -= 1
        vs[h[si[top]]] = N - sl[top] - 1
    results = []
    for M, K in queries:
        s = vs[K]
        if M > 1 and s >= M:
            results.append(s)
        else:
            results.append(1)
    return results

def stress():
    max_n = 12
    num_tests = 10000
    for test_num in range(num_tests):
        n = random.randint(1, max_n)
        h = list(range(1, n + 1))
        random.shuffle(h)
        queries = []
        for _ in range(random.randint(1, 10)):
            M = random.randint(1, n)
            K = random.randint(1, n)
            queries.append((M, K))
        
        tp = solve_two_pass(h, n, queries)
        sp = solve_single_pass(h, n, queries)
        
        for qi, (M, K) in enumerate(queries):
            bf = brute_force(h, M, K)
            if tp[qi] != bf or sp[qi] != bf:
                print(f"MISMATCH test {test_num}!")
                print(f"  n={n}, h={h}")
                print(f"  M={M}, K={K}")
                print(f"  brute_force={bf}, two_pass={tp[qi]}, single_pass={sp[qi]}")
                return False
    print(f"All {num_tests} tests passed!")
    return True

stress()

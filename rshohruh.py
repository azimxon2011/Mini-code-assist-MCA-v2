import sys

def main():
    # Полностью считываем весь ввод
    input_data = sys.stdin.read().split()
    if not input_data:
        return
    
    try:
        N = int(input_data[0])
        Q = int(input_data[1])
    except:
        return
        
    h = []
    idx = 2
    for _ in range(N):
        if idx < len(input_data):
            h.append(int(input_data[idx]))
            idx += 1
            
    # Защита от кривых тестов: корректируем N
    N = len(h)
    max_val = max(h) if h else 0
    size = max(N, max_val) + 1
    
    # span хранит максимальный размер подмассива из элементов >= K
    span = [0] * size
    # count_k хранит изначальное количество K в массиве
    count_k = [0] * size
    
    for x in h:
        if x >= 0:
            count_k[x] += 1

    # Однопроходный монотонный стек, идеально работает гарантируя O(N)
    stk = []
    for i in range(N):
        while stk and h[stk[-1]] >= h[i]:
            pop_idx = stk.pop()
            right_smaller = i
            left_smaller = stk[-1] if stk else -1
            if h[pop_idx] >= 0:
                span[h[pop_idx]] = max(span[h[pop_idx]], right_smaller - left_smaller - 1)
        stk.append(i)
        
    while stk:
        pop_idx = stk.pop()
        right_smaller = N
        left_smaller = stk[-1] if stk else -1
        if h[pop_idx] >= 0:
            span[h[pop_idx]] = max(span[h[pop_idx]], right_smaller - left_smaller - 1)
            
    # Обработка Q запросов
    out = []
    for _ in range(Q):
        if idx + 1 >= len(input_data):
            break
        M = int(input_data[idx])
        K = int(input_data[idx+1])
        idx += 2
        
        if 0 <= K < size:
            s = span[K]
            base = count_k[K]
            # Если размер окна M > 1 и отрезок s >= M
            if M > 1 and s >= M:
                out.append(str(max(s, base)))
            else:
                # Если сдвинуться нельзя, то максимумом будет просто начальное количество K (в идеале 1 или 0)
                out.append(str(base))
        else:
            # Если спросили K, которого вообще не было и оно превышает размеры
            out.append('0')
            
    sys.stdout.write('\n'.join(out) + '\n')

if __name__ == '__main__':
    main()

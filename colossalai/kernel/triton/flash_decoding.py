# Applying Flash-Decoding as descibed in
# https://pytorch.org/blog/flash-decoding/
# by Tri Dao, 2023
import torch
import triton
import triton.language as tl


# Triton 2.1.0
@triton.jit
def _flash_decoding_fwd_kernel(
    Q,  # [batch_size, head_num, q_len(1), head_dim]
    KCache,  # [num_blocks, num_kv_heads, head_dim, block_size]
    VCache,  # [num_blocks, num_kv_heads, head_dim, block_size]
    block_tables,  # [batch_size, max_blocks_per_sequence]
    mid_o,  # [batch_size, head_num, kv_split_num, head_dim]
    mid_o_lse,  # [batch_size, head_num, kv_split_num]
    kv_seq_len,  # [batch_size]
    batch_size,
    stride_qt,
    stride_qh,
    stride_qd,
    stride_cacheb,
    stride_cacheh,
    stride_cached,
    stride_cachebs,
    stride_bts,
    stride_btb,
    stride_mid_ot,
    stride_mid_oh,
    stride_mid_ob,
    stride_mid_od,
    stride_mid_o_lset,
    stride_mid_o_lseh,
    stride_mid_o_lseb,
    sm_scale,
    KV_GROUPS: tl.constexpr,
    BLOCK_KV: tl.constexpr,
    BLOCK_SIZE: tl.constexpr,
    HEAD_DIM: tl.constexpr,
):
    cur_seq_idx = tl.program_id(0)
    if cur_seq_idx >= batch_size:
        return
    cur_head_idx = tl.program_id(1)
    block_start_kv = tl.program_id(2)  # for splitting k/v

    cur_kv_head_idx = cur_head_idx // KV_GROUPS
    offsets_dmodel = tl.arange(0, HEAD_DIM)

    # NOTE It requires BLOCK_KV and BLOCK_SIZE to be the same
    # TODO might want to replace with BLOCK_KV % BLOCK_SIZE == 0 (optimize BLOCK_KV as multiple of BLOCK_SIZE)
    #      and then support calculating multiple kv cache blocks on an instance
    tl.static_assert(BLOCK_KV == BLOCK_SIZE)

    # get the current (kv) sequence length from provided context lengths tensor
    cur_kv_seq_len = tl.load(kv_seq_len + cur_seq_idx)

    offsets_q = cur_seq_idx * stride_qt + cur_head_idx * stride_qh + offsets_dmodel * stride_qd
    q = tl.load(Q + offsets_q)

    # block table for the current sequence
    block_table_ptr = block_tables + cur_seq_idx * stride_bts

    # actually current block table current block start idx
    # cur_bt_start_idx = block_start_kv * (BLOCK_KV // BLOCK_SIZE)
    cur_bt_start_idx = block_start_kv
    cur_block_id = tl.load(block_table_ptr + cur_bt_start_idx * stride_btb)

    if block_start_kv * BLOCK_KV >= cur_kv_seq_len:
        return

    cur_occupied_size = tl.where(
        (block_start_kv + 1) * BLOCK_SIZE <= cur_kv_seq_len, BLOCK_SIZE, cur_kv_seq_len - block_start_kv * BLOCK_SIZE
    )
    tl.device_assert(cur_occupied_size >= 0)

    offset_kvcache = cur_block_id * stride_cacheb + cur_kv_head_idx * stride_cacheh

    K_block_ptr = tl.make_block_ptr(
        base=KCache + offset_kvcache,
        shape=(HEAD_DIM, cur_occupied_size),
        strides=(stride_cached, stride_cachebs),
        offsets=(0, 0),
        block_shape=(HEAD_DIM, BLOCK_SIZE),
        order=(0, 1),
    )
    V_block_ptr = tl.make_block_ptr(
        base=VCache + offset_kvcache,
        shape=(HEAD_DIM, cur_occupied_size),
        strides=(stride_cached, stride_cachebs),
        offsets=(0, 0),
        block_shape=(HEAD_DIM, BLOCK_SIZE),
        order=(0, 1),
    )
    k_cur_block = tl.load(K_block_ptr)
    v_cur_block = tl.load(V_block_ptr)
    acc = tl.zeros([HEAD_DIM], dtype=tl.float32)
    # use block size of the paged/blocked kv cache
    S_ij = tl.zeros([BLOCK_SIZE], dtype=tl.float32)

    # NOTE a trick to come across triton's requirement that values in both first and second input shapes must be >= 16,
    # Multiplying two tensors with shapes [1, d] * [d, block_size] will fail.
    # Refer to https://github.com/openai/triton/discussions/895
    S_ij += tl.sum(q[:, None] * k_cur_block, 0)
    S_ij *= sm_scale
    S_ij += tl.where(block_start_kv * BLOCK_KV + tl.arange(0, BLOCK_SIZE) < cur_kv_seq_len, 0, float("-inf"))

    m = tl.max(S_ij, 0)
    S_ij -= m
    p_ij_hat = tl.exp(S_ij)
    l = tl.sum(p_ij_hat, 0)
    p_ij_hat = p_ij_hat.to(v_cur_block.type.element_ty)
    acc += tl.sum(v_cur_block * p_ij_hat[None, :], 1)
    acc = acc / l

    offsets_mid_o = (
        cur_seq_idx * stride_mid_ot
        + cur_head_idx * stride_mid_oh
        + block_start_kv * stride_mid_ob
        + offsets_dmodel * stride_mid_od
    )
    tl.store(mid_o + offsets_mid_o, acc)
    offsets_mid_o_lse = (
        cur_seq_idx * stride_mid_o_lset + cur_head_idx * stride_mid_o_lseh + block_start_kv * stride_mid_o_lseb
    )
    # logsumexp L^(j) = m^(j) + log(l^(j))
    tl.store(mid_o_lse + offsets_mid_o_lse, m + tl.log(l))


# Triton 2.1.0
@triton.jit
def _flash_decoding_fwd_reduce_kernel(
    mid_o,  # [batch_size, head_num, kv_split_num, head_dim]
    mid_o_lse,  # [batch_size, head_num, kv_split_num]
    O,  # [batch_size, num_heads, head_dim] or [batch_size, 1, num_heads, head_dim]
    kv_seq_len,
    batch_size,
    stride_mid_ot,
    stride_mid_oh,
    stride_mid_ob,
    stride_mid_od,
    stride_o_lset,
    stride_o_lseh,
    stride_o_lseb,
    stride_ob,
    stride_ol,
    stride_oh,
    stride_od,
    BLOCK_KV: tl.constexpr,
    HEAD_DIM: tl.constexpr,
):
    cur_seq_idx = tl.program_id(0)
    if cur_seq_idx >= batch_size:
        return
    cur_head_idx = tl.program_id(1)

    cur_kv_seq_len = tl.load(kv_seq_len + cur_seq_idx)
    offsets_dmodel = tl.arange(0, HEAD_DIM)

    # NOTE currently the block size BLOCK_KV splitting kv is relatively small as we have
    # BLOCK_KV == BLOCK_SIZE for now. We might want to decrease the number of blocks of kv splitted.
    kv_split_num = (cur_kv_seq_len + BLOCK_KV - 1) // BLOCK_KV
    m_i = float("-inf")  # max logic
    l = 0.0  # sum exp
    acc = tl.zeros([HEAD_DIM], dtype=tl.float32)

    offsets_mid_o = cur_seq_idx * stride_mid_ot + cur_head_idx * stride_mid_oh + offsets_dmodel
    offset_mid_lse = cur_seq_idx * stride_o_lset + cur_head_idx * stride_o_lseh
    for block_i in range(0, kv_split_num, 1):
        mid_o_block = tl.load(mid_o + offsets_mid_o + block_i * stride_mid_ob)
        lse = tl.load(mid_o_lse + offset_mid_lse + block_i * stride_o_lseb)
        m_ij = tl.maximum(m_i, lse)
        scale = tl.exp(m_i - m_ij)
        acc = acc * scale
        lse -= m_ij
        exp_logic = tl.exp(lse)
        acc += exp_logic * mid_o_block
        l = scale * l + exp_logic
        m_i = m_ij

    acc = acc / l
    offsets_O = cur_seq_idx * stride_ob + cur_head_idx * stride_oh + offsets_dmodel
    tl.store(O + offsets_O, acc.to(O.type.element_ty))
    return


# Decoding Stage
# Used with blocked KV Cache (PagedAttention)
def flash_decoding_attention(
    q: torch.Tensor,
    k_cache: torch.Tensor,
    v_cache: torch.Tensor,
    kv_seq_len: torch.Tensor,
    block_tables: torch.Tensor,
    block_size: int,
    max_seq_len_in_batch: int = None,
    output: torch.Tensor = None,
    mid_output: torch.Tensor = None,
    mid_output_lse: torch.Tensor = None,
    sm_scale: int = None,
    kv_group_num: int = 1,
):
    """
    Flash decoding implemented with a blocked KV Cache (PagedAttention) during decoding stage.

    Args:
        q (torch.Tensor):       [bsz, num_heads, head_dim]
        k_cache (torch.Tensor): [num_blocks, num_kv_heads, head_dim, block_size]
        v_cache (torch.Tensor): [num_blocks, num_kv_heads, head_dim, block_size]
        kv_seq_len (torch.Tensor): [batch_size]
            records the (kv) sequence lengths incorporating past kv sequence lengths.
        block_tables (torch.Tensor): [batch_size, max_blocks_per_sequence]
        max_seq_len_in_batch (int): Maximum sequence length in the batch.
        output (torch.Tensor):  [bsz, 1, num_heads, head_dim]
        mid_output (torch.Tensor): [ max_bsz , num_heads, kv_max_split_num, head_dim]
            Intermediate output tensor. `max_bsz` should be greater than or equal to `bsz`.
        mid_output_lse (torch.Tensor): [ max_bsz , num_heads, kv_max_split_num]
            Log-sum-exp of intermediate output. `max_bsz` should be greater than or equal to `bsz`.
        block_size (int): Size of each block in the blocked key/value cache.
        num_kv_group (int, optional): Number of key/value groups. Defaults to 1.

    Returns:
        Output tensor with shape [bsz, num_heads, q_len, head_dim]
    """
    q = q.squeeze() if q.dim() == 4 else q
    assert q.dim() == 3, f"Incompatible q dim: {q.dim()}"
    bsz, num_heads, head_dim = q.shape

    assert head_dim in {32, 64, 128, 256}
    assert kv_seq_len.shape[0] == block_tables.shape[0] == bsz, (
        f"Got incompatible batch size (number of seqs):\n"
        f"  KV seq lengths bsz {kv_seq_len.shape[0]}, Block tables bsz {block_tables.shape[0]}, "
        f"batch size {bsz}"
    )
    assert k_cache.size(-1) == v_cache.size(-1) == block_size, (
        f"Got incompatible block size on kv caches:\n"
        f"  assigned block_size {block_size}, k_cache block_size {k_cache.size(-1)}, "
        f"v_cache block_size {v_cache.size(-1)}"
    )

    # NOTE BLOCK_KV could be considered as block splitting the sequence on k/v
    # For now, BLOCK_KV is supposed to be equivalent with the size of physical cache block (i.e.`block_size`)
    assert block_size in {16, 32, 64, 128}
    BLOCK_KV = block_size

    sm_scale = 1.0 / (head_dim**0.5) if sm_scale is None else sm_scale
    max_seq_len_in_batch = kv_seq_len.max().item() if max_seq_len_in_batch is None else max_seq_len_in_batch
    # For compatibility (TODO revise modeling in future)
    kv_max_split_num = (max_seq_len_in_batch + BLOCK_KV - 1) // BLOCK_KV
    mid_output = (
        torch.zeros(size=(bsz, num_heads, kv_max_split_num, head_dim), dtype=torch.float32, device=q.device)
        if mid_output is None
        else mid_output
    )
    mid_output_lse = (
        torch.zeros(size=(bsz, num_heads, kv_max_split_num), dtype=torch.float32, device=q.device)
        if mid_output_lse is None
        else mid_output_lse
    )

    # NOTE use `triton.next_power_of_2` here to utilize the cache mechanism of triton
    # To optimize, revise batching/scheduling to batch 2^n sequences in a batch (preferred)
    grid = (triton.next_power_of_2(bsz), num_heads, triton.cdiv(triton.next_power_of_2(max_seq_len_in_batch), BLOCK_KV))
    _flash_decoding_fwd_kernel[grid](
        q,
        k_cache,
        v_cache,
        block_tables,
        mid_output,
        mid_output_lse,
        kv_seq_len,
        bsz,
        q.stride(0),
        q.stride(1),
        q.stride(2),
        k_cache.stride(0),
        k_cache.stride(1),
        k_cache.stride(2),
        k_cache.stride(3),
        block_tables.stride(0),
        block_tables.stride(1),
        mid_output.stride(0),
        mid_output.stride(1),
        mid_output.stride(2),
        mid_output.stride(3),
        mid_output_lse.stride(0),
        mid_output_lse.stride(1),
        mid_output_lse.stride(2),
        sm_scale,
        KV_GROUPS=kv_group_num,
        BLOCK_KV=block_size,
        BLOCK_SIZE=block_size,
        HEAD_DIM=head_dim,
    )

    output = torch.empty((bsz, 1, num_heads, head_dim), dtype=q.dtype, device=q.device) if output is None else output

    grid = (triton.next_power_of_2(bsz), num_heads)

    _flash_decoding_fwd_reduce_kernel[grid](
        mid_output,
        mid_output_lse,
        output,
        kv_seq_len,
        bsz,
        mid_output.stride(0),
        mid_output.stride(1),
        mid_output.stride(2),
        mid_output.stride(3),
        mid_output_lse.stride(0),
        mid_output_lse.stride(1),
        mid_output_lse.stride(2),
        output.stride(0),
        output.stride(1),
        output.stride(2),
        output.stride(3),
        BLOCK_KV=block_size,
        HEAD_DIM=head_dim,
    )

    return output

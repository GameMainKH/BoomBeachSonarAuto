# Linux x86_64 syscall-only loader, no Android libc/NDK dependency.
# ensure|check PIN UID EXPECTED_TAG_HEX
# check never creates a program or pin. ensure never replaces an existing pin.
# BPF_OBJ_GET_INFO_BY_FD verifies type + kernel SHA1 tag before accepting it.
.intel_syntax noprefix
.section .text
.global _start
_start:
    mov r15, rsp
    cmp qword ptr [r15], 5
    jne usage
    mov rax, [r15+16]
    xor r14d, r14d
    cmp dword ptr [rax], 0x75736e65  # ensu
    jne check_mode
    cmp word ptr [rax+4], 0x6572    # re
    jne usage
    cmp byte ptr [rax+6], 0
    jne usage
    mov r14d, 1
    jmp arguments
check_mode:
    cmp dword ptr [rax], 0x63656863  # chec
    jne usage
    cmp byte ptr [rax+4], 'k'
    jne usage
    cmp byte ptr [rax+5], 0
    jne usage
arguments:
    mov rsi, [r15+32]
    xor eax, eax
    xor ecx, ecx
uid_loop:
    movzx edx, byte ptr [rsi]
    test dl, dl
    jz uid_done
    sub edx, 48
    cmp edx, 9
    ja usage
    imul rax, rax, 10
    add rax, rdx
    cmp rax, 0x7fffffff
    ja usage
    inc rsi
    inc ecx
    jmp uid_loop
uid_done:
    test ecx, ecx
    jz usage
    mov dword ptr [rip+insns+12], eax
    mov rsi, [r15+40]
    lea rdi, [rip+expected_tag]
    mov ecx, 8
tag_loop:
    call hex_nibble
    shl al, 4
    mov bl, al
    call hex_nibble
    or al, bl
    mov [rdi], al
    inc rdi
    loop tag_loop
    cmp byte ptr [rsi], 0
    jne usage
    mov rax, [r15+24]
    mov [rip+obj_attr], rax
open_pin:
    mov eax, 321
    mov edi, 7                    # BPF_OBJ_GET
    lea rsi, [rip+obj_attr]
    mov edx, 16
    syscall
    test rax, rax
    jns verify_fd
    cmp eax, -2                  # ENOENT is the only load trigger
    jne syscall_error
    test r14d, r14d
    jz syscall_error
    lea rax, [rip+insns]
    mov [rip+load_attr+8], rax
    lea rax, [rip+license]
    mov [rip+load_attr+16], rax
    lea rax, [rip+verifier_log]
    mov [rip+load_attr+32], rax
    mov eax, 321
    mov edi, 5                    # BPF_PROG_LOAD, SOCKET_FILTER
    lea rsi, [rip+load_attr]
    mov edx, 72
    syscall
    test rax, rax
    js load_error
    mov r13d, 1                  # pin only after identity validation
    jmp verify_program
verify_fd:
    xor r13d, r13d
verify_program:
    mov r12d, eax
    mov [rip+info_attr], eax
    lea rax, [rip+prog_info]
    mov [rip+info_attr+8], rax
    mov eax, 321
    mov edi, 15                   # BPF_OBJ_GET_INFO_BY_FD
    lea rsi, [rip+info_attr]
    mov edx, 16
    syscall
    test rax, rax
    js syscall_error
    cmp dword ptr [rip+prog_info], 1
    jne identity_error
    mov rax, [rip+prog_info+8]
    cmp rax, [rip+expected_tag]
    jne identity_error
    test r13d, r13d
    jz success
    mov [rip+obj_attr+8], r12d
    mov eax, 321
    mov edi, 6                    # BPF_OBJ_PIN; never unlink/replace a live object
    lea rsi, [rip+obj_attr]
    mov edx, 16
    syscall
    test rax, rax
    jns success
    cmp eax, -17                 # EEXIST race: validate the winner once
    jne syscall_error
    xor r14d, r14d
    jmp open_pin
success:
    lea rsi, [rip+ok_msg]
    mov edx, OFFSET ok_len
    mov eax, 1
    mov edi, 1
    syscall
    xor edi, edi
    jmp exit
hex_nibble:
    movzx eax, byte ptr [rsi]
    inc rsi
    sub eax, 48
    cmp eax, 9
    jbe hex_return
    sub eax, 49
    cmp eax, 5
    ja usage
    add eax, 10
hex_return:
    ret
load_error:
    mov r12, rax
    lea rsi, [rip+verifier_log]
    xor edx, edx
log_loop:
    cmp byte ptr [rsi+rdx], 0
    je log_write
    inc edx
    cmp edx, 4096
    jb log_loop
log_write:
    mov eax, 1
    mov edi, 2
    syscall
    mov rax, r12
syscall_error:
    neg eax
    mov edi, eax
    jmp exit
identity_error:
    lea rsi, [rip+bad_msg]
    mov edx, OFFSET bad_len
    mov eax, 1
    mov edi, 2
    syscall
    mov edi, 65
    jmp exit
usage:
    mov edi, 64
exit:
    mov eax, 60
    syscall

# A separate RW segment starts here. Code is RX, never writable.
.balign 4096,0
insns:
    .byte 0x85,0
    .short 0
    .long 47                     # bpf_get_socket_uid(skb)
    .byte 0x15,0
    .short 2
    .long 0                      # patched UID, restricted to signed 31-bit
    .byte 0xb7,0
    .short 0
    .long 0
    .byte 0x95,0
    .short 0
    .long 0
    .byte 0xb7,0
    .short 0
    .long 1
    .byte 0x95,0
    .short 0
    .long 0
load_attr:
    .long 1,6
    .quad 0,0
    .long 1,4096
    .quad 0
    .zero 32
obj_attr:
    .zero 16
info_attr:
    .long 0,128
    .quad 0
prog_info:
    .zero 128
expected_tag:
    .zero 8
license:
    .asciz "GPL"
ok_msg:
    .ascii "BBSA_UID_BPF_V1_OK\n"
.set ok_len, .-ok_msg
bad_msg:
    .ascii "BPF_IDENTITY_MISMATCH\n"
.set bad_len, .-bad_msg
verifier_log:
    .zero 4096

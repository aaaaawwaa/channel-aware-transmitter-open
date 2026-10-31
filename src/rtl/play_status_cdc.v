`timescale 1ns / 1ps

module play_status_cdc (
    input wire src_clk,
    input wire src_rst_n,
    input wire [31:0] status_src,
    input wire dst_clk,
    input wire dst_rst_n,
    output reg [31:0] status_dst
);
    reg [31:0] snapshot_src;
    reg update_toggle_src;
    reg toggle_meta_dst;
    reg toggle_sync_dst;
    reg toggle_seen_dst;
    reg [31:0] data_meta_dst;
    reg [31:0] data_sync_dst;

    always @(posedge src_clk or negedge src_rst_n) begin
        if (!src_rst_n) begin
            snapshot_src <= 32'd0;
            update_toggle_src <= 1'b0;
        end else if (status_src != snapshot_src) begin
            snapshot_src <= status_src;
            update_toggle_src <= ~update_toggle_src;
        end
    end

    always @(posedge dst_clk or negedge dst_rst_n) begin
        if (!dst_rst_n) begin
            toggle_meta_dst <= 1'b0;
            toggle_sync_dst <= 1'b0;
            toggle_seen_dst <= 1'b0;
            data_meta_dst <= 32'd0;
            data_sync_dst <= 32'd0;
            status_dst <= 32'd0;
        end else begin
            toggle_meta_dst <= update_toggle_src;
            toggle_sync_dst <= toggle_meta_dst;
            data_meta_dst <= snapshot_src;
            data_sync_dst <= data_meta_dst;
            if (toggle_sync_dst != toggle_seen_dst) begin
                status_dst <= data_sync_dst;
                toggle_seen_dst <= toggle_sync_dst;
            end
        end
    end
endmodule

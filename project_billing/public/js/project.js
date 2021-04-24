frappe.ui.form.on('Project', {
    refresh: function (frm) {
        if (frm.doc.total_billed_amount > 0) {
            frm.set_df_property('retention_percentage', 'read_only', 1);
            frm.set_df_property('percent_complete_method', 'read_only', 1);
        }
    }
});